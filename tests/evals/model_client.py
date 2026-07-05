"""Minimal OpenAI-compatible chat client (stdlib only) for local model endpoints.

Works against any OpenAI-compatible server (vLLM, LM Studio, llama.cpp) — no
vLLM-specific behavior lives here. The endpoint is never hardcoded; it always
comes from env vars via client_from_env(), because the eval runs only where
the operator's endpoint is reachable (never in CI or cloud sandboxes).
"""

from __future__ import annotations

import json
import os
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, fields, replace
from pathlib import Path

from tests.evals.eval_errors import EvalError

_DEFAULT_FINISH_REASON = "stop"


class ModelClientError(EvalError):
    """Endpoint unreachable, HTTP error, timeout, or malformed response."""

    def __init__(self, message: str, elapsed_s: float = 0.0) -> None:
        super().__init__(message)
        self.elapsed_s = elapsed_s


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
    elapsed_s: float = 0.0


@dataclass
class SamplingParams:
    """Request-time sampling knobs sent on every /chat/completions call.

    Defaults are Alibaba's recommended Qwen3 "instruct, no thinking" preset.
    Callers can override individual fields via a TOML config file and/or
    explicit values (e.g. CLI flags) — see resolve_sampling_params() for the
    precedence between the two.
    """

    temperature: float = 0.7
    top_p: float = 0.80
    top_k: int = 20
    min_p: float = 0.0
    presence_penalty: float = 1.5
    repetition_penalty: float = 1.0
    max_tokens: int = 2048


_SAMPLING_FIELD_NAMES = tuple(f.name for f in fields(SamplingParams))


def load_sampling_overrides(config_path: Path) -> dict[str, object]:
    """Read the [sampling] table from a TOML config file.

    Only keys matching a SamplingParams field are allowed — an unknown key
    (typo, wrong section) raises rather than being silently ignored.
    """
    try:
        with config_path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ModelClientError(f"invalid TOML in {config_path}: {exc}") from exc
    except OSError as exc:
        raise ModelClientError(f"cannot read {config_path}: {exc}") from exc

    table = data.get("sampling", {})
    unknown = set(table) - set(_SAMPLING_FIELD_NAMES)
    if unknown:
        raise ModelClientError(
            f"{config_path}: unknown [sampling] key(s) {sorted(unknown)}; "
            f"expected one of {_SAMPLING_FIELD_NAMES}"
        )
    return dict(table)


def resolve_sampling_params(
    config_path: Path | None = None,
    overrides: dict[str, object] | None = None,
) -> SamplingParams:
    """Merge sampling params with precedence: built-in defaults < config file
    < explicit overrides (e.g. CLI flags). `overrides` values that are None
    are treated as "not provided" and don't participate.
    """
    merged: dict[str, object] = {}
    if config_path is not None:
        merged.update(load_sampling_overrides(config_path))
    if overrides:
        merged.update({k: v for k, v in overrides.items() if v is not None})
    return replace(SamplingParams(), **merged) if merged else SamplingParams()


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
        timeout: float = 1800.0,
        disable_thinking: bool = False,
        sampling: SamplingParams | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        # Reasoning models (e.g. Qwen3) otherwise spend part of max_tokens on
        # a <think> block before the real answer; opt-in since a non-reasoning
        # or non-Qwen endpoint may not recognize chat_template_kwargs at all.
        self.disable_thinking = disable_thinking
        self.sampling = sampling if sampling is not None else SamplingParams()

    def chat(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatReply:
        payload: dict[str, object] = {
            "model": self.model,
            "messages": messages,
            "temperature": (
                temperature if temperature is not None else self.sampling.temperature
            ),
            "max_tokens": (
                max_tokens if max_tokens is not None else self.sampling.max_tokens
            ),
            "top_p": self.sampling.top_p,
            "top_k": self.sampling.top_k,
            "min_p": self.sampling.min_p,
            "presence_penalty": self.sampling.presence_penalty,
            "repetition_penalty": self.sampling.repetition_penalty,
        }
        if tools is not None:
            payload["tools"] = tools
        if self.disable_thinking:
            payload["chat_template_kwargs"] = {"enable_thinking": False}

        headers = {"Content-Type": "application/json"}
        if self.api_key is not None:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        start = time.monotonic()
        try:
            with urllib.request.urlopen(  # nosec: B310 (operator-supplied local endpoint)
                req, timeout=self.timeout
            ) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise ModelClientError(
                f"HTTP {exc.code} from {self.base_url}: {exc.reason}",
                elapsed_s=time.monotonic() - start,
            ) from exc
        except TimeoutError as exc:
            raise ModelClientError(
                f"timeout calling {self.base_url}: {exc}",
                elapsed_s=time.monotonic() - start,
            ) from exc
        except urllib.error.URLError as exc:
            raise ModelClientError(
                f"connection error calling {self.base_url}: {exc.reason}",
                elapsed_s=time.monotonic() - start,
            ) from exc
        elapsed_s = time.monotonic() - start

        try:
            data = json.loads(body)
            message = data["choices"][0]["message"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise ModelClientError(
                f"malformed response body: {body!r}", elapsed_s=elapsed_s
            ) from exc

        raw_tool_calls = message.get("tool_calls") or []
        return ChatReply(
            content=message.get("content"),
            tool_calls=_parse_tool_calls(raw_tool_calls),
            finish_reason=data["choices"][0].get(
                "finish_reason", _DEFAULT_FINISH_REASON
            ),
            elapsed_s=elapsed_s,
        )


def fetch_model_info(
    base_url: str, model: str, api_key: str | None = None
) -> dict[str, object]:
    """GET {base_url}/models (standard OpenAI API surface) and return the
    entry matching `model`'s id. Raises ModelClientError if unreachable, the
    model id isn't listed, or the response is malformed.
    """
    url = base_url.rstrip("/") + "/models"
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(  # nosec: B310 (operator-supplied local endpoint)
            req, timeout=30
        ) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ModelClientError(f"failed to fetch model info from {url}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ModelClientError(f"malformed /models response from {url}: {exc}") from exc

    for entry in body.get("data", []):
        if entry.get("id") == model:
            return dict(entry)
    raise ModelClientError(f"model {model!r} not found in {url} response")


def client_from_env(sampling: SamplingParams | None = None) -> ChatClient:
    """Build a ChatClient from SYND_EVAL_BASE_URL / SYND_EVAL_MODEL /
    SYND_EVAL_API_KEY / SYND_EVAL_DISABLE_THINKING. Raises ModelClientError
    naming any missing required var.

    `sampling` is accepted (not read from env) so callers with their own
    config-file/CLI-flag precedence (see resolve_sampling_params) can inject
    the resolved SamplingParams; defaults to the built-in preset otherwise.
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
    disable_thinking = os.environ.get("SYND_EVAL_DISABLE_THINKING", "") in (
        "1",
        "true",
        "True",
    )
    return ChatClient(
        base_url=base_url,
        model=model,
        api_key=api_key,
        disable_thinking=disable_thinking,
        sampling=sampling,
    )
