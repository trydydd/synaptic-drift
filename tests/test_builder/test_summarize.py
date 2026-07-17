"""Tests for the LLM summary enrichment build path (D31).

A real local HTTP server plays the OpenAI-compatible endpoint so the code
under test exercises its actual network stack; only the model is fake.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable

import pytest

from synd.builder.build import build_pack
from synd.builder.manifest import load_manifest
from synd.builder.summarize import (
    CURRENT_PROMPT_VERSION,
    LlmSummarizerConfig,
    read_lockfile,
)
from synd.errors import SummarizerError


# -- fake vLLM endpoint -------------------------------------------------------


class _FakeLlm:
    """Local OpenAI-compatible /chat/completions server with a scriptable reply.

    reply_fn(prompt_text) -> str body of the assistant message, or raises to
    simulate a server-side failure for that request.
    """

    def __init__(self, reply_fn: Callable[[str], str]) -> None:
        self.reply_fn = reply_fn
        self.requests_served = 0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server API
                length = int(self.headers["Content-Length"])
                payload = json.loads(self.rfile.read(length))
                prompt = payload["messages"][0]["content"]
                fake.requests_served += 1
                try:
                    content = fake.reply_fn(prompt)
                except Exception:
                    self.send_response(500)
                    self.end_headers()
                    self.wfile.write(b"boom")
                    return
                body = json.dumps(
                    {
                        "choices": [
                            {
                                "message": {"role": "assistant", "content": content},
                                "finish_reason": "stop",
                            }
                        ]
                    }
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}/v1"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture()
def fake_llm() -> Any:
    servers: list[_FakeLlm] = []

    def _start(reply_fn: Callable[[str], str]) -> _FakeLlm:
        server = _FakeLlm(reply_fn)
        servers.append(server)
        return server

    yield _start
    for server in servers:
        server.close()


@pytest.fixture()
def source_dir(tmp_path: Path) -> Path:
    src = tmp_path / "docs"
    src.mkdir()
    (src / "auth.md").write_text(
        "# Authentication\n\nUse the token helper to sign requests.\n",
        encoding="utf-8",
    )
    (src / "errors.md").write_text(
        "# Errors\n\nEvery failure maps to a typed error class.\n",
        encoding="utf-8",
    )
    return src


def _config(server: _FakeLlm, lockfile: Path) -> LlmSummarizerConfig:
    return LlmSummarizerConfig(
        base_url=server.base_url,
        model="fake-model",
        api_key="",
        lockfile_path=lockfile,
    )


def _read_chunks(ctx_path: Path) -> list[dict[str, Any]]:
    import zipfile

    with zipfile.ZipFile(ctx_path) as zf:
        lines = zf.read("chunks.jsonl").decode("utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]


# -- happy path ---------------------------------------------------------------


def test_llm_build_appends_sentence_to_heuristic_summary(
    fake_llm: Any, source_dir: Path, tmp_path: Path
) -> None:
    server = fake_llm(lambda prompt: "Developers can sign requests with a token.")
    ctx_path, _ = build_pack(
        package="lib",
        version="1.0.0",
        source=source_dir,
        output=tmp_path / "out",
        summarizer="llm",
        summarizer_config=_config(server, tmp_path / "lib.summaries.jsonl"),
    )
    chunks = _read_chunks(ctx_path)
    assert chunks, "pack has chunks"
    for chunk in chunks:
        # Append format (D30): heuristic sentence first, LLM sentence after.
        assert chunk["summary"].endswith("Developers can sign requests with a token.")
        assert chunk["summary"] != "Developers can sign requests with a token."


def test_llm_build_records_provenance_in_manifest(
    fake_llm: Any, source_dir: Path, tmp_path: Path
) -> None:
    server = fake_llm(lambda prompt: "A sentence.")
    ctx_path, _ = build_pack(
        package="lib",
        version="1.0.0",
        source=source_dir,
        output=tmp_path / "out",
        summarizer="llm",
        summarizer_config=_config(server, tmp_path / "lib.summaries.jsonl"),
    )
    manifest = load_manifest(ctx_path)
    assert manifest["summarizer"] == "llm"
    assert manifest["summarizer_model"] == "fake-model"
    assert manifest["summarizer_prompt_version"] == CURRENT_PROMPT_VERSION


def test_heuristic_default_build_is_unchanged(source_dir: Path, tmp_path: Path) -> None:
    """No summarizer flag: no LLM fields in the manifest, no network anything."""
    ctx_path, _ = build_pack(
        package="lib",
        version="1.0.0",
        source=source_dir,
        output=tmp_path / "out",
    )
    manifest = load_manifest(ctx_path)
    assert "summarizer" not in manifest
    assert "summarizer_model" not in manifest


# -- lockfile: warm rebuilds, resumability, invalidation ----------------------


def test_warm_rebuild_makes_no_llm_calls_and_reproduces_chunks(
    fake_llm: Any, source_dir: Path, tmp_path: Path
) -> None:
    import zipfile

    server = fake_llm(lambda prompt: "A stable sentence.")
    lockfile = tmp_path / "lib.summaries.jsonl"

    ctx_path, _ = build_pack(
        package="lib",
        version="1.0.0",
        source=source_dir,
        output=tmp_path / "out1",
        summarizer="llm",
        summarizer_config=_config(server, lockfile),
    )
    calls_after_first = server.requests_served
    assert calls_after_first > 0

    ctx_path2, _ = build_pack(
        package="lib",
        version="1.0.0",
        source=source_dir,
        output=tmp_path / "out2",
        summarizer="llm",
        summarizer_config=_config(server, lockfile),
    )
    assert server.requests_served == calls_after_first, "warm rebuild hit the endpoint"
    # D31 reproducibility: identical source + lockfile → byte-identical
    # chunks.jsonl (summaries included), with no model in the loop the second
    # time. (Full-archive digests additionally vary by the manifest's
    # created_at timestamp, which predates and is unrelated to summaries.)
    with zipfile.ZipFile(ctx_path) as z1, zipfile.ZipFile(ctx_path2) as z2:
        assert z1.read("chunks.jsonl") == z2.read("chunks.jsonl")


def test_lockfile_only_generates_missing_chunks(
    fake_llm: Any, source_dir: Path, tmp_path: Path
) -> None:
    server = fake_llm(lambda prompt: "A sentence.")
    lockfile = tmp_path / "lib.summaries.jsonl"
    build_pack(
        package="lib",
        version="1.0.0",
        source=source_dir,
        output=tmp_path / "out1",
        summarizer="llm",
        summarizer_config=_config(server, lockfile),
    )
    calls_first = server.requests_served

    (source_dir / "new.md").write_text(
        "# New Page\n\nFresh content that is not in the lockfile.\n", encoding="utf-8"
    )
    build_pack(
        package="lib",
        version="1.0.0",
        source=source_dir,
        output=tmp_path / "out2",
        summarizer="llm",
        summarizer_config=_config(server, lockfile),
    )
    # Only the new chunk(s) hit the endpoint.
    assert 0 < server.requests_served - calls_first < calls_first + 2


def test_lockfile_prompt_version_mismatch_fails_loudly(
    fake_llm: Any, source_dir: Path, tmp_path: Path
) -> None:
    server = fake_llm(lambda prompt: "A sentence.")
    lockfile = tmp_path / "lib.summaries.jsonl"
    lockfile.write_text(
        json.dumps({"prompt_version": "v999", "model": "fake-model"}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(SummarizerError, match="prompt_version"):
        build_pack(
            package="lib",
            version="1.0.0",
            source=source_dir,
            output=tmp_path / "out",
            summarizer="llm",
            summarizer_config=_config(server, lockfile),
        )


def test_lockfile_malformed_line_fails_loudly(
    fake_llm: Any, source_dir: Path, tmp_path: Path
) -> None:
    server = fake_llm(lambda prompt: "A sentence.")
    lockfile = tmp_path / "lib.summaries.jsonl"
    lockfile.write_text("not json\n", encoding="utf-8")
    with pytest.raises(SummarizerError):
        build_pack(
            package="lib",
            version="1.0.0",
            source=source_dir,
            output=tmp_path / "out",
            summarizer="llm",
            summarizer_config=_config(server, lockfile),
        )


# -- failure semantics: fail-hard, no partial pack ----------------------------


def test_unreachable_endpoint_fails_build_without_writing_pack(
    source_dir: Path, tmp_path: Path
) -> None:
    config = LlmSummarizerConfig(
        base_url="http://127.0.0.1:1/v1",  # nothing listens here
        model="fake-model",
        api_key="",
        lockfile_path=tmp_path / "lib.summaries.jsonl",
    )
    out = tmp_path / "out"
    with pytest.raises(SummarizerError):
        build_pack(
            package="lib",
            version="1.0.0",
            source=source_dir,
            output=out,
            summarizer="llm",
            summarizer_config=config,
        )
    assert not (out / "lib@1.0.0.ctx").exists()


def test_server_error_fails_but_persists_successes_for_retry(
    fake_llm: Any, source_dir: Path, tmp_path: Path
) -> None:
    """One chunk fails → build fails, but completed summaries land in the
    lockfile so the retry only regenerates the failed one."""
    state = {"failed_once": False}

    def flaky(prompt: str) -> str:
        if "typed error class" in prompt and not state["failed_once"]:
            state["failed_once"] = True
            raise RuntimeError("simulated failure")
        return "A sentence."

    server = fake_llm(flaky)
    lockfile = tmp_path / "lib.summaries.jsonl"
    with pytest.raises(SummarizerError, match="1 chunk"):
        build_pack(
            package="lib",
            version="1.0.0",
            source=source_dir,
            output=tmp_path / "out",
            summarizer="llm",
            summarizer_config=_config(server, lockfile),
        )
    persisted = read_lockfile(lockfile, model="fake-model")
    assert len(persisted) >= 1  # the successful chunk survived

    calls_before_retry = server.requests_served
    ctx_path, _ = build_pack(
        package="lib",
        version="1.0.0",
        source=source_dir,
        output=tmp_path / "out",
        summarizer="llm",
        summarizer_config=_config(server, lockfile),
    )
    assert ctx_path.exists()
    assert server.requests_served - calls_before_retry == 1


def test_empty_summary_from_model_fails(
    fake_llm: Any, source_dir: Path, tmp_path: Path
) -> None:
    server = fake_llm(lambda prompt: "   ")
    with pytest.raises(SummarizerError):
        build_pack(
            package="lib",
            version="1.0.0",
            source=source_dir,
            output=tmp_path / "out",
            summarizer="llm",
            summarizer_config=_config(server, tmp_path / "lib.summaries.jsonl"),
        )


def test_degenerate_oversized_summary_fails(
    fake_llm: Any, source_dir: Path, tmp_path: Path
) -> None:
    server = fake_llm(lambda prompt: "word " * 400)
    with pytest.raises(SummarizerError):
        build_pack(
            package="lib",
            version="1.0.0",
            source=source_dir,
            output=tmp_path / "out",
            summarizer="llm",
            summarizer_config=_config(server, tmp_path / "lib.summaries.jsonl"),
        )


def test_llm_summarizer_without_config_is_a_programming_error(
    source_dir: Path, tmp_path: Path
) -> None:
    with pytest.raises(SummarizerError, match="config"):
        build_pack(
            package="lib",
            version="1.0.0",
            source=source_dir,
            output=tmp_path / "out",
            summarizer="llm",
        )


# -- CLI surface --------------------------------------------------------------


def test_cli_llm_without_endpoint_is_usage_error(
    source_dir: Path, tmp_path: Path
) -> None:
    from click.testing import CliRunner

    from synd.cli.build import build as build_cmd

    runner = CliRunner()
    result = runner.invoke(
        build_cmd,
        [
            "lib@1.0.0",
            "--source",
            str(source_dir),
            "--output",
            str(tmp_path / "out"),
            "--summarizer",
            "llm",
        ],
        env={"SYND_SUMMARIZER_URL": "", "SYND_SUMMARIZER_MODEL": ""},
    )
    assert result.exit_code == 2
    assert "--summarizer-url" in result.output


def test_cli_llm_build_with_env_config(
    fake_llm: Any, source_dir: Path, tmp_path: Path
) -> None:
    from click.testing import CliRunner

    from synd.cli.build import build as build_cmd

    server = fake_llm(lambda prompt: "A sentence from the CLI path.")
    out = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(
        build_cmd,
        [
            "lib@1.0.0",
            "--source",
            str(source_dir),
            "--output",
            str(out),
            "--summarizer",
            "llm",
        ],
        env={
            "SYND_SUMMARIZER_URL": server.base_url,
            "SYND_SUMMARIZER_MODEL": "fake-model",
        },
    )
    assert result.exit_code == 0, result.output
    manifest = load_manifest(out / "lib@1.0.0.ctx")
    assert manifest["summarizer"] == "llm"
    # Default lockfile lands next to the pack.
    assert (out / "lib@1.0.0.summaries.jsonl").exists()


def test_cli_unreachable_endpoint_exits_with_build_code(
    source_dir: Path, tmp_path: Path
) -> None:
    from click.testing import CliRunner

    from synd.cli.build import build as build_cmd

    runner = CliRunner()
    result = runner.invoke(
        build_cmd,
        [
            "lib@1.0.0",
            "--source",
            str(source_dir),
            "--output",
            str(tmp_path / "out"),
            "--summarizer",
            "llm",
            "--summarizer-url",
            "http://127.0.0.1:1/v1",
            "--summarizer-model",
            "fake-model",
        ],
    )
    assert result.exit_code == 6  # SummarizerError → BuildError → EXIT_BUILD
