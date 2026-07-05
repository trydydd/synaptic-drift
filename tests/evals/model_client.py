"""Minimal OpenAI-compatible chat client (stdlib only) for local model endpoints.

Works against any OpenAI-compatible server (vLLM, LM Studio, llama.cpp) — no
vLLM-specific behavior lives here. The endpoint is never hardcoded; it always
comes from env vars via client_from_env(), because the eval runs only where
the operator's endpoint is reachable (never in CI or cloud sandboxes).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from tests.evals.eval_errors import EvalError

_DEFAULT_FINISH_REASON = "stop"


class ModelClientError(EvalError):
    """Endpoint unreachable, HTTP error, timeout, or malformed response."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, object]


@dataclass
class ChatReply:
    content: str | None
    tool_calls: list[ToolCall]
    finish_reason: str


def _parse_tool_calls(raw_tool_calls: list[dict[str, object]]) -> list[ToolCall]:
    tool_calls: list[ToolCall] = []
    for raw in raw_tool_calls:
        function = raw["function"]
        assert isinstance(function, dict)
        raw_arguments = function["arguments"]
        try:
            arguments = json.loads(raw_arguments)  # type: ignore[arg-type]
        except (json.JSONDecodeError, TypeError) as exc:
            raise ModelClientError(
                f"tool call arguments are not valid JSON: {raw_arguments!r}"
            ) from exc
        tool_calls.append(
            ToolCall(
                id=str(raw["id"]),
                name=str(function["name"]),
                arguments=arguments,
            )
        )
    return tool_calls


class ChatClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def chat(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> ChatReply:
        payload: dict[str, object] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools is not None:
            payload["tools"] = tools

        headers = {"Content-Type": "application/json"}
        if self.api_key is not None:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(  # nosec: B310 (operator-supplied local endpoint)
                req, timeout=self.timeout
            ) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise ModelClientError(
                f"HTTP {exc.code} from {self.base_url}: {exc.reason}"
            ) from exc
        except TimeoutError as exc:
            raise ModelClientError(f"timeout calling {self.base_url}: {exc}") from exc
        except urllib.error.URLError as exc:
            raise ModelClientError(
                f"connection error calling {self.base_url}: {exc.reason}"
            ) from exc

        try:
            data = json.loads(body)
            message = data["choices"][0]["message"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise ModelClientError(f"malformed response body: {body!r}") from exc

        raw_tool_calls = message.get("tool_calls") or []
        return ChatReply(
            content=message.get("content"),
            tool_calls=_parse_tool_calls(raw_tool_calls),
            finish_reason=data["choices"][0].get(
                "finish_reason", _DEFAULT_FINISH_REASON
            ),
        )


def client_from_env() -> ChatClient:
    """Build a ChatClient from SYND_EVAL_BASE_URL / SYND_EVAL_MODEL /
    SYND_EVAL_API_KEY. Raises ModelClientError naming any missing required var.
    """
    base_url = os.environ.get("SYND_EVAL_BASE_URL")
    model = os.environ.get("SYND_EVAL_MODEL")
    missing = [
        name
        for name, value in (
            ("SYND_EVAL_BASE_URL", base_url),
            ("SYND_EVAL_MODEL", model),
        )
        if not value
    ]
    if missing:
        raise ModelClientError(
            f"missing required environment variable(s): {', '.join(missing)}"
        )
    assert base_url is not None and model is not None
    api_key = os.environ.get("SYND_EVAL_API_KEY")
    return ChatClient(base_url=base_url, model=model, api_key=api_key)
