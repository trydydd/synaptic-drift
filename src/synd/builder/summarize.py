"""LLM summary enrichment for `synd build --summarizer llm` (D31).

Rewrites each chunk's summary as `<heuristic sentence> <LLM sentence>` — the
append format that won the D30 measurement ladder: the heuristic first
sentence keeps the chunk's own tokens as exact-match capital while the LLM
sentence adds vocabulary-normalized prose, lifting paraphrase and
vocabulary-mismatch retrieval without taxing direct queries.

Reproducibility (amends D5's scope — see decisions.md D31): greedy decoding
is necessary but not sufficient for byte-stable output across engine
versions and hardware, so reproducibility is anchored in a **summary
lockfile** instead. The lockfile maps `content_hash → summary`; rebuilds
reuse cached summaries byte-for-byte and the model runs only for chunks
whose content changed. Identical source + identical lockfile → identical
pack bytes, with no model in the loop at all on warm rebuilds.

Lockfile format (JSONL): a header line pinning `prompt_version` and `model`,
then one `{"content_hash": ..., "summary": ...}` record per chunk. A header
mismatch is a hard error — changing the prompt or the model is a deliberate
act that regenerates every summary, and the operator signals it by removing
(or pointing away from) the old lockfile.

Failure semantics: fail hard (SummarizerError → exit 6), never silently mix
LLM and heuristic summaries in one pack. Successes are flushed to the
lockfile as they complete, so a failed run retries cheaply.

This is a build-time feature only. Chunk content is sent to the
publisher-configured endpoint; nothing here runs at query time.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from synd.errors import SummarizerError

CURRENT_PROMPT_VERSION = "v1"

# v1 is the measured prompt: strict improvement on both gold corpora
# (decisions.md D30). The v3/v4 grounding variants measurably regressed the
# BM25 path — any new version must pass its own full matrix gate before
# being added here, and bumping CURRENT_PROMPT_VERSION invalidates every
# existing lockfile by design.
PROMPTS: dict[str, str] = {
    "v1": (
        "Write ONE sentence (max 30 words) describing what a developer can "
        "do or learn from this documentation excerpt. Use plain developer "
        "vocabulary AND the library's own terms for the key concepts, so "
        "both phrasings are present. No preamble, no markdown, just the "
        "sentence.\n"
        "\n"
        "Section: {heading_path}\n"
        "\n"
        "Excerpt:\n"
        "{content}\n"
    ),
}

_MAX_TOKENS = 90
_CONTENT_CHARS = 3000  # prompt budget; BM25 and embeddings both favor the lead
_MAX_SUMMARY_CHARS = 600  # degenerate-output guard; well above the 30-word ask
_REQUEST_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class LlmSummarizerConfig:
    """Endpoint and lockfile settings for an LLM-summarized build."""

    base_url: str
    model: str
    api_key: str
    lockfile_path: Path
    concurrency: int = 8


def read_lockfile(path: Path, model: str) -> dict[str, str]:
    """Load `content_hash → summary` from a summary lockfile.

    Returns {} when the file does not exist. Raises SummarizerError when the
    header pins a different prompt_version or model than this build uses, or
    when any line is malformed — a corrupt lockfile must never silently
    degrade into a full regeneration (digest churn) or a partial one.
    """
    if not path.exists():
        return {}
    summaries: dict[str, str] = {}
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SummarizerError(
                    f"malformed summary lockfile {path} (line {line_no}): {exc}"
                ) from exc
            if line_no == 1:
                _check_header(record, path, model)
                continue
            try:
                summaries[record["content_hash"]] = record["summary"]
            except (KeyError, TypeError) as exc:
                raise SummarizerError(
                    f"malformed summary lockfile {path} (line {line_no}): "
                    f"expected content_hash/summary record"
                ) from exc
    return summaries


def _check_header(record: object, path: Path, model: str) -> None:
    if not isinstance(record, dict) or "prompt_version" not in record:
        raise SummarizerError(
            f"summary lockfile {path} has no prompt_version header line; "
            "it was not written by synd build — remove it to regenerate"
        )
    if record["prompt_version"] != CURRENT_PROMPT_VERSION:
        raise SummarizerError(
            f"summary lockfile {path} was generated with prompt_version "
            f"{record['prompt_version']!r}; this build uses "
            f"{CURRENT_PROMPT_VERSION!r}. A prompt change regenerates every "
            "summary and every pack digest — if that is intended, remove the "
            "lockfile and rebuild"
        )
    if record.get("model") != model:
        raise SummarizerError(
            f"summary lockfile {path} was generated with model "
            f"{record.get('model')!r}; this build uses {model!r}. Remove the "
            "lockfile to regenerate with the new model"
        )


def generate_summaries(
    chunks_by_hash: dict[str, tuple[str, str]],
    config: LlmSummarizerConfig,
) -> dict[str, str]:
    """Return `content_hash → LLM sentence` for every requested chunk.

    chunks_by_hash maps content_hash → (heading_path, content). Hashes
    already present in the lockfile are served from it without touching the
    endpoint; misses are generated concurrently and appended to the lockfile
    as they complete. Any failure raises SummarizerError after all other
    generations have been persisted.
    """
    cached = read_lockfile(config.lockfile_path, model=config.model)
    todo = sorted(h for h in chunks_by_hash if h not in cached)
    if not todo:
        return {h: cached[h] for h in chunks_by_hash}

    prompt_template = PROMPTS[CURRENT_PROMPT_VERSION]
    is_new_file = not config.lockfile_path.exists()
    config.lockfile_path.parent.mkdir(parents=True, exist_ok=True)

    failures: list[tuple[str, str]] = []
    with config.lockfile_path.open("a", encoding="utf-8") as out:
        if is_new_file:
            header = {
                "prompt_version": CURRENT_PROMPT_VERSION,
                "model": config.model,
            }
            out.write(json.dumps(header, sort_keys=True) + "\n")
            out.flush()

        def _one(content_hash: str) -> tuple[str, str]:
            heading_path, content = chunks_by_hash[content_hash]
            prompt = prompt_template.format(
                heading_path=heading_path, content=content[:_CONTENT_CHARS]
            )
            return content_hash, _call_llm(prompt, config)

        with ThreadPoolExecutor(max_workers=config.concurrency) as pool:
            futures = {pool.submit(_one, h): h for h in todo}
            for future in as_completed(futures):
                content_hash = futures[future]
                try:
                    _, summary = future.result()
                except SummarizerError as exc:
                    failures.append((content_hash, str(exc)))
                    continue
                record = {"content_hash": content_hash, "summary": summary}
                out.write(json.dumps(record, sort_keys=True) + "\n")
                out.flush()
                cached[content_hash] = summary

    if failures:
        first_hash, first_error = failures[0]
        raise SummarizerError(
            f"summary generation failed for {len(failures)} chunk(s) "
            f"(first: {first_hash[:19]}…: {first_error}). "
            f"{len(todo) - len(failures)} completed summaries are cached in "
            f"{config.lockfile_path} — re-run the build to retry only the "
            "failures"
        )
    return {h: cached[h] for h in chunks_by_hash}


def _call_llm(prompt: str, config: LlmSummarizerConfig) -> str:
    """One greedy chat completion; raises SummarizerError on any failure."""
    url = config.base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": config.model,
        "max_tokens": _MAX_TOKENS,
        "temperature": 0.0,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {"content-type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(
            request, timeout=_REQUEST_TIMEOUT_SECONDS
        ) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise SummarizerError(f"summarizer endpoint request failed: {exc}") from exc

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise SummarizerError(
            f"summarizer endpoint returned an unexpected response shape: {exc}"
        ) from exc

    summary = " ".join((content or "").split())
    if not summary:
        raise SummarizerError("summarizer returned an empty summary")
    if len(summary) > _MAX_SUMMARY_CHARS:
        raise SummarizerError(
            f"summarizer returned a degenerate {len(summary)}-char summary "
            f"(limit {_MAX_SUMMARY_CHARS})"
        )
    return summary
