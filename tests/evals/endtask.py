"""End-task A/B driver: agent loop over search/fetch, per-arm pass rates.

Answers the project's central question: does giving a small model synd's
doc retrieval improve its code output? Each task runs in two arms:

  no_docs   — the model gets only the task prompt. No tools, one turn.
  with_docs — the model additionally gets 'search' and 'fetch' tools, whose
              schemas mirror the real MCP server's tools. Tool calls are
              dispatched IN-PROCESS to synd.server.search_docs / fetch_docs
              against the eval_db fixture — no MCP transport, no subprocess,
              same public API, deterministic.

Grading is entirely static (tests/evals/tasks.py's grade()) — model output is
never executed. A live run against a real model endpoint is env-gated via
tests/evals/model_client.py's client_from_env() and is not reachable in CI.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from synd.search.fts import SearchError
from synd.server import fetch_docs, search_docs
from synd.storage.db import Database
from tests.evals.eval_errors import EvalError
from tests.evals.model_client import ChatReply, ModelClientError, ToolCall
from tests.evals.tasks import EvalTask, TaskSet, grade

SEARCH_TOOL_SCHEMA: dict[str, object] = {
    "type": "function",
    "function": {
        "name": "search",
        "description": (
            "Returns summaries and chunk_ids for matching docs — no content. "
            "Call fetch with the chunk_ids to get full text. Terms are "
            "matched independently and ranked by relevance — a chunk "
            "matching more of your terms ranks higher, but doesn't need to "
            "match all of them. You can use a few distinctive terms or a "
            "natural-language question; both work."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search terms or a natural-language question.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results to return.",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
}

FETCH_TOOL_SCHEMA: dict[str, object] = {
    "type": "function",
    "function": {
        "name": "fetch",
        "description": "Returns full content for chunk_ids obtained from search.",
        "parameters": {
            "type": "object",
            "properties": {
                "chunk_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Chunk IDs to fetch full content for.",
                },
            },
            "required": ["chunk_ids"],
        },
    },
}

# Note: search's semantics described here (and to the model in
# SYSTEM_PROMPT_WITH_DOCS below) reflect the current OR+BM25 backend
# (decision D29, docs/decisions.md) — terms are ranked, not required to all
# co-occur. This differs from the AND-semantics wording this eval was
# originally scoped against (see chunk-e8's local_context in
# .work/ledger.yaml, verified 2026-06-11); that wording is now stale
# relative to the shipped product and would mislead the model about how
# search actually behaves today.
SYSTEM_PROMPT_WITH_DOCS = (
    "You are a coding assistant with NO prior knowledge of the FastMCP API. "
    "Your memory of FastMCP is unreliable and outdated. You MUST call the "
    "search tool before answering, and base your code ONLY on fetched "
    "documentation. Search matches your terms independently and ranks "
    "results by relevance — you can use a few distinctive terms or a "
    "natural-language question, either works; your terms don't all need to "
    "appear together in one chunk. When search returns results, IMMEDIATELY "
    "fetch the most relevant chunk_ids — never repeat a similar search. Once "
    "you have fetched relevant documentation, give your final answer. Answer "
    "with Python code in a fenced code block."
)

SYSTEM_PROMPT_NO_DOCS = (
    "You are a coding assistant. Answer with Python code in a fenced code block."
)


class ChatClientProtocol(Protocol):
    def chat(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> ChatReply: ...


@dataclass
class TaskRunResult:
    task_id: str
    arm: str
    passed: bool
    failures: list[str]
    turns_used: int
    tool_calls_made: int
    reply_chars: int
    error: str | None


def dispatch_tool_call(db: Database, package: str, call: ToolCall) -> str:
    """Dispatch one tool call to the real public search/fetch API.

    Returns a JSON string suitable for a role='tool' message content. Unknown
    tool names and SearchError both return an error payload rather than
    raising — the model gets feedback and may recover, instead of the whole
    run crashing on one hallucinated tool name or malformed query.
    """
    if call.name == "search":
        try:
            result = search_docs(
                db,
                query=str(call.arguments["query"]),
                packages=[package],
                limit=int(call.arguments.get("limit", 5)),  # type: ignore[arg-type]
            )
        except SearchError as exc:
            return json.dumps({"error": str(exc), "results": []})
        return json.dumps(result)
    if call.name == "fetch":
        chunk_ids = [int(i) for i in call.arguments["chunk_ids"]]  # type: ignore[union-attr]
        result = fetch_docs(db, chunk_ids=chunk_ids)
        return json.dumps(result)
    return json.dumps({"error": f"unknown tool: {call.name}"})


def _tool_call_message(reply: ChatReply) -> dict[str, object]:
    return {
        "role": "assistant",
        "content": reply.content,
        "tool_calls": [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
            }
            for c in reply.tool_calls
        ],
    }


def run_task(
    client: ChatClientProtocol,
    db: Database | None,
    task: EvalTask,
    package: str,
    arm: str,
    max_turns: int = 8,
) -> TaskRunResult:
    """Execute one task in one arm and return a graded result.

    no_docs: no tools, a single turn. with_docs: 'search'/'fetch' tools
    dispatched against db, looping until the model stops calling tools or
    max_turns is reached (error='max_turns' — the loop always terminates,
    even against a model that never stops calling tools).
    """
    if arm == "no_docs":
        tools: list[dict[str, object]] | None = None
        system_prompt = SYSTEM_PROMPT_NO_DOCS
    elif arm == "with_docs":
        if db is None:
            raise EvalError("with_docs arm requires a db")
        tools = [SEARCH_TOOL_SCHEMA, FETCH_TOOL_SCHEMA]
        system_prompt = SYSTEM_PROMPT_WITH_DOCS
    else:
        raise EvalError(f"unknown arm: {arm!r}")

    messages: list[dict[str, object]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task.prompt},
    ]

    tool_calls_made = 0
    turns_used = 0
    try:
        for turn in range(1, max_turns + 1):
            turns_used = turn
            reply = client.chat(messages, tools=tools)

            if not reply.tool_calls:
                grade_result = grade(reply.content or "", task)
                return TaskRunResult(
                    task_id=task.id,
                    arm=arm,
                    passed=grade_result.passed,
                    failures=grade_result.failures,
                    turns_used=turns_used,
                    tool_calls_made=tool_calls_made,
                    reply_chars=len(reply.content or ""),
                    error=None,
                )

            messages.append(_tool_call_message(reply))
            assert db is not None  # with_docs is the only arm offering tools
            for call in reply.tool_calls:
                tool_calls_made += 1
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": dispatch_tool_call(db, package, call),
                    }
                )
    except ModelClientError as exc:
        return TaskRunResult(
            task_id=task.id,
            arm=arm,
            passed=False,
            failures=[],
            turns_used=turns_used,
            tool_calls_made=tool_calls_made,
            reply_chars=0,
            error=str(exc),
        )

    return TaskRunResult(
        task_id=task.id,
        arm=arm,
        passed=False,
        failures=[],
        turns_used=max_turns,
        tool_calls_made=tool_calls_made,
        reply_chars=0,
        error="max_turns",
    )


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True
        ).strip()
    except Exception:
        return "unknown"


def run_endtask_eval(
    client: ChatClientProtocol,
    db: Database,
    taskset: TaskSet,
    reps: int = 1,
    max_turns: int = 8,
) -> dict[str, object]:
    """Execute every task in both arms, `reps` times each, and aggregate."""
    per_task: list[dict[str, object]] = []
    arm_results: dict[str, list[bool]] = {"no_docs": [], "with_docs": []}

    for arm in ("no_docs", "with_docs"):
        for task in taskset.tasks:
            for rep in range(reps):
                result = run_task(
                    client,
                    db,
                    task,
                    taskset.package,
                    arm,
                    max_turns=max_turns,
                )
                arm_results[arm].append(result.passed)
                per_task.append(
                    {
                        "task_id": result.task_id,
                        "arm": result.arm,
                        "rep": rep,
                        "passed": result.passed,
                        "failures": result.failures,
                        "turns_used": result.turns_used,
                        "tool_calls_made": result.tool_calls_made,
                        "reply_chars": result.reply_chars,
                        "error": result.error,
                    }
                )

    def _arm_summary(passed_flags: list[bool]) -> dict[str, object]:
        n = len(passed_flags)
        pass_rate = round(sum(passed_flags) / n, 4) if n else 0.0
        return {"pass_rate": pass_rate, "n": n}

    return {
        "meta": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "git_commit": _git_commit(),
            "model": getattr(client, "model", "fake"),
            "reps": reps,
            "max_turns": max_turns,
            "task_count": len(taskset.tasks),
        },
        "arms": {
            "no_docs": _arm_summary(arm_results["no_docs"]),
            "with_docs": _arm_summary(arm_results["with_docs"]),
        },
        "per_task": per_task,
    }
