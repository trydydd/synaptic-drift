"""End-task definitions loader and static graders.

End-task evals give a model a coding task; the reply is graded STATICALLY —
regex patterns over the extracted python code, plus an ast.parse syntax
check. Model-generated code is NEVER executed, imported, eval'd, or exec'd:
this is a hard security boundary (replies come from an untrusted model and
may contain arbitrary side effects), not a style preference.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.evals.eval_errors import EvalDatasetError

_FENCE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)


@dataclass
class Graders:
    must_parse: bool
    must_match: list[str]
    must_not_match: list[str]


@dataclass
class EvalTask:
    id: str
    prompt: str
    graders: Graders
    teaches: list[str]


@dataclass
class TaskSet:
    schema_version: int
    package: str
    version: str | None
    tasks: list[EvalTask]


def _require(obj: dict[str, Any], field: str, task_id: str, path: Path) -> Any:
    if field not in obj:
        raise EvalDatasetError(
            f"{path}: task {task_id!r} missing required field {field!r}"
        )
    return obj[field]


def _load_graders(raw: dict[str, Any], task_id: str, path: Path) -> Graders:
    return Graders(
        must_parse=bool(_require(raw, "must_parse", task_id, path)),
        must_match=list(_require(raw, "must_match", task_id, path)),
        must_not_match=list(_require(raw, "must_not_match", task_id, path)),
    )


def _load_task(raw: dict[str, Any], path: Path) -> EvalTask:
    task_id = _require(raw, "id", "<unknown>", path)
    prompt = _require(raw, "prompt", task_id, path)
    graders_raw = _require(raw, "graders", task_id, path)
    teaches = _require(raw, "teaches", task_id, path)
    return EvalTask(
        id=task_id,
        prompt=prompt,
        graders=_load_graders(graders_raw, task_id, path),
        teaches=list(teaches),
    )


def load_tasks(path: Path) -> TaskSet:
    """Parse a committed task file into typed dataclasses.

    Raises EvalDatasetError for a missing/invalid file, a missing required
    field, or duplicate task ids.
    """
    if not path.exists():
        raise EvalDatasetError(f"task file not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EvalDatasetError(f"{path}: invalid JSON: {exc}") from exc

    schema_version = _require(raw, "schema_version", "<taskset>", path)
    corpus = _require(raw, "corpus", "<taskset>", path)
    tasks_raw = _require(raw, "tasks", "<taskset>", path)

    tasks: list[EvalTask] = []
    seen_ids: set[str] = set()
    for t_raw in tasks_raw:
        task = _load_task(t_raw, path)
        if task.id in seen_ids:
            raise EvalDatasetError(f"{path}: duplicate task id {task.id!r}")
        seen_ids.add(task.id)
        tasks.append(task)

    return TaskSet(
        schema_version=schema_version,
        package=corpus["package"],
        version=corpus.get("version"),
        tasks=tasks,
    )


def extract_python_code(reply: str) -> str:
    """Extract fenced python code blocks from a model reply.

    Extracts all blocks fenced with ```python, ```py, or bare ```; joins them
    with newlines. If the reply contains no fences at all, the entire reply
    is treated as code — small models sometimes skip fencing, and a
    fence-only extractor would grade an empty string for those replies and
    silently fail every criterion for the wrong reason.
    """
    blocks = _FENCE_RE.findall(reply)
    if not blocks:
        return reply
    return "\n".join(blocks)


@dataclass
class GradeResult:
    passed: bool
    failures: list[str]


def grade(reply: str, task: EvalTask) -> GradeResult:
    """Apply must_parse / must_match / must_not_match to reply's extracted code.

    Runs every criterion and reports every failure — no short-circuit;
    partial failure info is the analysis payload. ast.parse is the only
    operation performed on extracted code; it compiles to an AST and never
    executes anything.
    """
    code = extract_python_code(reply)
    failures: list[str] = []

    if task.graders.must_parse:
        try:
            ast.parse(code)
        except SyntaxError as exc:
            failures.append(f"must_parse: {exc}")

    for pattern in task.graders.must_match:
        if not re.search(pattern, code):
            failures.append(f"must_match: {pattern}")

    for pattern in task.graders.must_not_match:
        if re.search(pattern, code):
            failures.append(f"must_not_match: {pattern}")

    return GradeResult(passed=not failures, failures=failures)
