from __future__ import annotations

import copy
import json

import pytest

from synd.storage.db import Database
from tests.evals.conftest import EVAL_PACKAGE
from tests.evals.endtask import (
    FETCH_TOOL_SCHEMA,
    SEARCH_TOOL_SCHEMA,
    dispatch_tool_call,
    run_endtask_eval,
    run_task,
)
from tests.evals.model_client import ChatReply, ModelClientError, ToolCall
from tests.evals.tasks import EvalTask, Graders

pytestmark = pytest.mark.evals


class FakeChatClient:
    """Scripted list[ChatReply]; records every chat() call's messages/tools."""

    def __init__(self, replies: list[ChatReply], model: str = "fake") -> None:
        self._replies = list(replies)
        self.model = model
        self.calls: list[dict[str, object]] = []

    def chat(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> ChatReply:
        if not self._replies:
            raise AssertionError(
                "FakeChatClient exhausted: scripted fewer replies than turns taken"
            )
        self.calls.append(
            {"messages": copy.deepcopy(messages), "tools": copy.deepcopy(tools)}
        )
        return self._replies.pop(0)


class _RaisingChatClient:
    """Raises ModelClientError on the first call — simulates a dead endpoint."""

    model = "raising-fake"

    def chat(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> ChatReply:
        raise ModelClientError("connection refused")


def _task(task_id: str = "t1", must_match: list[str] | None = None) -> EvalTask:
    return EvalTask(
        id=task_id,
        prompt="write a function",
        graders=Graders(
            must_parse=True, must_match=must_match or [], must_not_match=[]
        ),
        teaches=["some / heading"],
    )


def _text_reply(content: str) -> ChatReply:
    return ChatReply(content=content, tool_calls=[], finish_reason="stop")


def _tool_call_reply(
    name: str, arguments: dict[str, object], call_id: str = "1"
) -> ChatReply:
    return ChatReply(
        content=None,
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
        finish_reason="tool_calls",
    )


def test_no_docs_arm_sends_no_tools() -> None:
    client = FakeChatClient([_text_reply("```python\ndef f():\n    return 1\n```")])
    result = run_task(client, None, _task(), EVAL_PACKAGE, "no_docs")

    assert client.calls[0]["tools"] is None
    assert result.passed


def test_with_docs_arm_sends_both_schemas(eval_db: Database) -> None:
    client = FakeChatClient([_text_reply("```python\ndef f():\n    return 1\n```")])
    run_task(client, eval_db, _task(), EVAL_PACKAGE, "with_docs")

    assert client.calls[0]["tools"] == [SEARCH_TOOL_SCHEMA, FETCH_TOOL_SCHEMA]


def test_tool_call_dispatched_to_search_docs(eval_db: Database) -> None:
    call = ToolCall(id="1", name="search", arguments={"query": "tool"})
    result_str = dispatch_tool_call(eval_db, EVAL_PACKAGE, call)
    payload = json.loads(result_str)

    assert "results" in payload
    assert len(payload["results"]) > 0
    assert "chunk_id" in payload["results"][0]


def test_tool_results_appended_as_tool_messages(eval_db: Database) -> None:
    client = FakeChatClient(
        [
            _tool_call_reply("search", {"query": "tool"}, call_id="abc"),
            _text_reply("```python\ndef f():\n    return 1\n```"),
        ]
    )
    run_task(client, eval_db, _task(), EVAL_PACKAGE, "with_docs")

    second_call_messages = client.calls[1]["messages"]
    assert isinstance(second_call_messages, list)
    tool_messages = [m for m in second_call_messages if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "abc"
    assert "results" in json.loads(tool_messages[0]["content"])


def test_final_reply_is_graded() -> None:
    client = FakeChatClient([_text_reply("```python\ndef f():\n    return 1\n```")])
    result = run_task(
        client, None, _task(must_match=[r"def f\("]), EVAL_PACKAGE, "no_docs"
    )

    assert result.passed
    assert result.failures == []


def test_model_client_error_recorded_not_raised() -> None:
    client = _RaisingChatClient()
    result = run_task(client, None, _task(), EVAL_PACKAGE, "no_docs")

    assert not result.passed
    assert result.error is not None
    assert "connection refused" in result.error


def test_pass_rates_aggregated_per_arm(eval_db: Database) -> None:
    from tests.evals.tasks import TaskSet

    passing = _task("t_pass", must_match=[r"def f\("])
    failing = _task("t_fail", must_match=[r"def never_matches\("])
    taskset = TaskSet(
        schema_version=1,
        package=EVAL_PACKAGE,
        version="1.0.0",
        tasks=[passing, failing],
    )

    reply = _text_reply("```python\ndef f():\n    return 1\n```")
    # 2 tasks x 2 arms x 1 rep = 4 run_task calls, each a single no-tool-call turn
    client = FakeChatClient([reply, reply, reply, reply])

    payload = run_endtask_eval(client, eval_db, taskset, reps=1)

    assert payload["arms"]["no_docs"]["pass_rate"] == 0.5
    assert payload["arms"]["no_docs"]["n"] == 2
    assert payload["arms"]["with_docs"]["pass_rate"] == 0.5
    assert payload["arms"]["with_docs"]["n"] == 2


def test_loop_terminates_at_max_turns(eval_db: Database) -> None:
    """NEG: a client that always returns tool_calls must stop at max_turns
    with error='max_turns', not hang."""
    replies = [_tool_call_reply("search", {"query": "tool"}) for _ in range(10)]
    client = FakeChatClient(replies)

    result = run_task(client, eval_db, _task(), EVAL_PACKAGE, "with_docs", max_turns=3)

    assert result.turns_used == 3
    assert result.error == "max_turns"
    assert not result.passed


def test_unknown_tool_name_returns_error_payload(eval_db: Database) -> None:
    """NEG: a hallucinated tool name must produce a JSON error tool-message,
    not crash the run."""
    call = ToolCall(id="1", name="nonsense", arguments={})
    result_str = dispatch_tool_call(eval_db, EVAL_PACKAGE, call)
    payload = json.loads(result_str)

    assert "unknown tool: nonsense" in payload["error"]


@pytest.mark.live_model
def test_endtask_eval_live() -> None:
    """Live run against a real model endpoint. Skips cleanly without env vars."""
    import os

    if not os.environ.get("SYND_EVAL_BASE_URL") or not os.environ.get(
        "SYND_EVAL_MODEL"
    ):
        pytest.skip(
            "SYND_EVAL_BASE_URL and SYND_EVAL_MODEL must both be set to run the "
            "live end-task eval, e.g.:\n"
            "  SYND_EVAL_BASE_URL=http://<your-vllm-host>:8000/v1 "
            "SYND_EVAL_MODEL=<served-model-name> "
            ".venv/bin/pytest tests/evals/test_endtask.py --evals --live-model -s"
        )

    import json as json_module
    from pathlib import Path

    from tests.evals.conftest import EVAL_CORPUS_DIR, EVAL_VERSION, load_ctx_into_db
    from tests.evals.model_client import client_from_env
    from tests.evals.tasks import load_tasks
    from synd.builder.build import build_pack

    client = client_from_env()

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        ctx_path, _ = build_pack(
            package=EVAL_PACKAGE,
            version=EVAL_VERSION,
            source=EVAL_CORPUS_DIR,
            output=tmp_path,
        )
        db = Database(tmp_path / "eval.db")
        db.create_schema()
        load_ctx_into_db(ctx_path, db)

        taskset = load_tasks(Path("tests/evals/datasets/tasks/seed_tasks.json"))
        payload = run_endtask_eval(client, db, taskset)
        db.close()

    results_dir = Path("tests/evals/results")
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "endtask_latest.json").write_text(
        json_module.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    print(json_module.dumps(payload["arms"], indent=2))
