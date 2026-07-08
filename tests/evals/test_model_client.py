from __future__ import annotations

import http.server
import json
import threading
from collections.abc import Iterator
from email.message import Message
from pathlib import Path

import pytest

from tests.evals.model_client import (
    ChatClient,
    ModelClientError,
    SamplingParams,
    client_from_env,
    fetch_model_info,
    load_sampling_overrides,
    resolve_sampling_params,
)


class _StubState:
    def __init__(self) -> None:
        self.status_code = 200
        self.response_body: dict[str, object] | str = {}
        self.last_request_body: dict[str, object] | None = None
        self.last_request_headers: Message | None = None
        self.get_response_body: dict[str, object] | str = {"data": []}
        self.last_get_path: str | None = None
        self.last_get_headers: Message | None = None


def _make_handler(stub: _StubState) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length)
            stub.last_request_body = json.loads(raw_body) if raw_body else None
            stub.last_request_headers = self.headers
            self.send_response(stub.status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            body = stub.response_body
            payload = body if isinstance(body, str) else json.dumps(body)
            self.wfile.write(payload.encode("utf-8"))

        def do_GET(self) -> None:
            stub.last_get_path = self.path
            stub.last_get_headers = self.headers
            self.send_response(stub.status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            body = stub.get_response_body
            payload = body if isinstance(body, str) else json.dumps(body)
            self.wfile.write(payload.encode("utf-8"))

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass  # silence default stderr request logging

    return Handler


@pytest.fixture
def stub_server() -> Iterator[tuple[str, _StubState]]:
    stub = _StubState()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(stub))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base_url = f"http://{host}:{port}/v1"
    try:
        yield base_url, stub
    finally:
        server.shutdown()
        server.server_close()


def _ok_reply(content: str = "ok") -> dict[str, object]:
    return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}


def test_chat_returns_text_reply(stub_server: tuple[str, _StubState]) -> None:
    base_url, stub = stub_server
    stub.response_body = _ok_reply("hello")
    client = ChatClient(base_url=base_url, model="test-model")

    reply = client.chat([{"role": "user", "content": "hi"}])

    assert reply.content == "hello"
    assert reply.tool_calls == []
    assert reply.finish_reason == "stop"


def test_chat_parses_tool_calls(stub_server: tuple[str, _StubState]) -> None:
    base_url, stub = stub_server
    stub.response_body = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "search",
                                "arguments": '{"query": "x"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }
    client = ChatClient(base_url=base_url, model="test-model")

    reply = client.chat([{"role": "user", "content": "hi"}])

    assert reply.content is None
    assert len(reply.tool_calls) == 1
    assert reply.tool_calls[0].id == "call_1"
    assert reply.tool_calls[0].name == "search"
    assert reply.tool_calls[0].arguments == {"query": "x"}
    assert reply.finish_reason == "tool_calls"


def test_tools_field_sent_only_when_provided(
    stub_server: tuple[str, _StubState],
) -> None:
    base_url, stub = stub_server
    stub.response_body = _ok_reply()
    client = ChatClient(base_url=base_url, model="test-model")

    client.chat([{"role": "user", "content": "hi"}])
    assert stub.last_request_body is not None
    assert "tools" not in stub.last_request_body

    schema = [{"type": "function", "function": {"name": "search", "parameters": {}}}]
    client.chat([{"role": "user", "content": "hi"}], tools=schema)
    assert stub.last_request_body["tools"] == schema


def test_disable_thinking_sends_chat_template_kwargs(
    stub_server: tuple[str, _StubState],
) -> None:
    base_url, stub = stub_server
    stub.response_body = _ok_reply()
    client = ChatClient(base_url=base_url, model="test-model")

    client.chat([{"role": "user", "content": "hi"}])
    assert stub.last_request_body is not None
    assert "chat_template_kwargs" not in stub.last_request_body

    thinking_off_client = ChatClient(
        base_url=base_url, model="test-model", disable_thinking=True
    )
    thinking_off_client.chat([{"role": "user", "content": "hi"}])
    assert stub.last_request_body["chat_template_kwargs"] == {"enable_thinking": False}


def test_client_from_env_disable_thinking_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SYND_EVAL_BASE_URL", "http://example.invalid/v1")
    monkeypatch.setenv("SYND_EVAL_MODEL", "test-model")
    monkeypatch.setenv("SYND_EVAL_DISABLE_THINKING", "1")

    client = client_from_env()
    assert client.disable_thinking is True


def test_fetch_model_info_returns_matching_entry(
    stub_server: tuple[str, _StubState],
) -> None:
    base_url, stub = stub_server
    stub.get_response_body = {
        "data": [
            {"id": "other-model", "root": "Other/Model"},
            {"id": "red", "root": "Qwen/Qwen3.6-27B-FP8", "max_model_len": 131072},
        ]
    }

    info = fetch_model_info(base_url, "red")

    assert info == {
        "id": "red",
        "root": "Qwen/Qwen3.6-27B-FP8",
        "max_model_len": 131072,
    }
    assert stub.last_get_path == "/v1/models"


def test_fetch_model_info_missing_model_raises(
    stub_server: tuple[str, _StubState],
) -> None:
    base_url, stub = stub_server
    stub.get_response_body = {"data": [{"id": "other-model"}]}

    with pytest.raises(ModelClientError, match="not found"):
        fetch_model_info(base_url, "red")


def test_default_sampling_params_match_alibaba_preset() -> None:
    defaults = SamplingParams()
    assert defaults.temperature == 0.7
    assert defaults.top_p == 0.80
    assert defaults.top_k == 20
    assert defaults.min_p == 0.0
    assert defaults.presence_penalty == 1.5
    assert defaults.repetition_penalty == 1.0


def test_chat_sends_full_sampling_payload(stub_server: tuple[str, _StubState]) -> None:
    base_url, stub = stub_server
    stub.response_body = _ok_reply()
    client = ChatClient(base_url=base_url, model="test-model")

    client.chat([{"role": "user", "content": "hi"}])

    assert stub.last_request_body is not None
    assert stub.last_request_body["temperature"] == 0.7
    assert stub.last_request_body["top_p"] == 0.80
    assert stub.last_request_body["top_k"] == 20
    assert stub.last_request_body["min_p"] == 0.0
    assert stub.last_request_body["presence_penalty"] == 1.5
    assert stub.last_request_body["repetition_penalty"] == 1.0
    assert stub.last_request_body["max_tokens"] == 2048


def test_resolve_sampling_params_precedence(tmp_path: Path) -> None:
    config = tmp_path / "sampling.toml"
    config.write_text("[sampling]\ntemperature = 0.3\ntop_p = 0.5\n")

    # No config, no overrides -> built-in defaults.
    assert resolve_sampling_params() == SamplingParams()

    # Config file alone overrides just the keys it names.
    from_config = resolve_sampling_params(config_path=config)
    assert from_config.temperature == 0.3
    assert from_config.top_p == 0.5
    assert from_config.top_k == 20  # untouched default

    # An explicit override (e.g. a CLI flag) beats the config file.
    from_both = resolve_sampling_params(
        config_path=config, overrides={"temperature": 0.9, "max_tokens": None}
    )
    assert from_both.temperature == 0.9  # override wins over config's 0.3
    assert from_both.top_p == 0.5  # config value still applies
    assert from_both.max_tokens == 2048  # None override is "not provided"


def test_load_sampling_overrides_rejects_unknown_key(tmp_path: Path) -> None:
    config = tmp_path / "sampling.toml"
    config.write_text("[sampling]\nnonsense_key = 1\n")

    with pytest.raises(ModelClientError, match="unknown"):
        load_sampling_overrides(config)


def test_load_sampling_overrides_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ModelClientError, match="cannot read"):
        load_sampling_overrides(tmp_path / "does-not-exist.toml")


def test_http_error_raises_model_client_error(
    stub_server: tuple[str, _StubState],
) -> None:
    base_url, stub = stub_server
    stub.status_code = 500
    stub.response_body = {"error": "internal error"}
    client = ChatClient(base_url=base_url, model="test-model")

    with pytest.raises(ModelClientError):
        client.chat([{"role": "user", "content": "hi"}])


def test_malformed_json_raises_model_client_error(
    stub_server: tuple[str, _StubState],
) -> None:
    base_url, stub = stub_server
    stub.response_body = "not valid json{{{"
    client = ChatClient(base_url=base_url, model="test-model")

    with pytest.raises(ModelClientError):
        client.chat([{"role": "user", "content": "hi"}])


def test_client_from_env_missing_vars_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SYND_EVAL_BASE_URL", raising=False)
    monkeypatch.delenv("SYND_EVAL_MODEL", raising=False)
    monkeypatch.delenv("SYND_EVAL_API_KEY", raising=False)

    with pytest.raises(ModelClientError, match="SYND_EVAL_BASE_URL"):
        client_from_env()


def test_no_auth_header_when_api_key_unset(
    stub_server: tuple[str, _StubState],
) -> None:
    """NEG: request must NOT contain an Authorization header unless api_key is set."""
    base_url, stub = stub_server
    stub.response_body = _ok_reply()

    client_no_key = ChatClient(base_url=base_url, model="test-model", api_key=None)
    client_no_key.chat([{"role": "user", "content": "hi"}])
    assert stub.last_request_headers is not None
    assert "Authorization" not in stub.last_request_headers

    client_with_key = ChatClient(base_url=base_url, model="test-model", api_key="k")
    client_with_key.chat([{"role": "user", "content": "hi"}])
    assert stub.last_request_headers["Authorization"] == "Bearer k"


def test_tool_call_arguments_json_string_decoded(
    stub_server: tuple[str, _StubState],
) -> None:
    """NEG: OpenAI-format arguments arrive as a JSON STRING; passing the raw
    string through instead of json.loads-ing it must not happen."""
    base_url, stub = stub_server
    stub.response_body = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "1",
                            "type": "function",
                            "function": {
                                "name": "search",
                                "arguments": '{"query": "x"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }
    client = ChatClient(base_url=base_url, model="test-model")

    reply = client.chat([{"role": "user", "content": "hi"}])

    assert reply.tool_calls[0].arguments == {"query": "x"}
    assert not isinstance(reply.tool_calls[0].arguments, str)
