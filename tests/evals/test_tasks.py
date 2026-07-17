from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.evals.eval_errors import EvalDatasetError
from tests.evals.tasks import EvalTask, Graders, extract_python_code, grade, load_tasks

_SEED_TASKS_PATH = Path("tests/evals/datasets/tasks/seed_tasks.json")


def test_load_committed_seed_tasks() -> None:
    taskset = load_tasks(_SEED_TASKS_PATH)
    assert len(taskset.tasks) == 10
    assert taskset.package == "evalcorpus"


def test_load_duplicate_task_ids_raises(tmp_path: Path) -> None:
    task = {
        "id": "t1",
        "prompt": "do something",
        "graders": {"must_parse": True, "must_match": [], "must_not_match": []},
        "teaches": ["some / heading"],
    }
    path = tmp_path / "dup.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "corpus": {"package": "evalcorpus", "version": "1.0.0"},
                "tasks": [task, task],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(EvalDatasetError):
        load_tasks(path)


def test_extract_single_python_fence() -> None:
    reply = "text\n```python\nx = 1\n```\nmore"
    result = extract_python_code(reply)
    assert "x = 1" in result
    assert "text" not in result


def test_extract_multiple_fences_joined() -> None:
    reply = "```python\na = 1\n```\nsome prose\n```python\nb = 2\n```"
    result = extract_python_code(reply)
    assert "a = 1" in result
    assert "b = 2" in result


def test_extract_no_fence_returns_whole_reply() -> None:
    reply = "just code, no fences: x = 1"
    assert extract_python_code(reply) == reply


def _task(
    must_parse: bool = True,
    must_match: list[str] | None = None,
    must_not_match: list[str] | None = None,
) -> EvalTask:
    return EvalTask(
        id="t1",
        prompt="prompt",
        graders=Graders(
            must_parse=must_parse,
            must_match=must_match or [],
            must_not_match=must_not_match or [],
        ),
        teaches=["some / heading"],
    )


def test_grade_passing_reply() -> None:
    task = _task(must_match=[r"def f\("])
    reply = "```python\ndef f():\n    return 1\n```"
    result = grade(reply, task)
    assert result.passed
    assert result.failures == []


def test_grade_missing_pattern_lists_failure() -> None:
    task = _task(must_match=[r"def missing_function\("])
    reply = "```python\ndef f():\n    return 1\n```"
    result = grade(reply, task)
    assert not result.passed
    assert any("def missing_function\\(" in f for f in result.failures)


def test_grade_syntax_error_fails_must_parse() -> None:
    task = _task(must_parse=True)
    reply = "```python\ndef f(:\n```"
    result = grade(reply, task)
    assert not result.passed
    assert any("must_parse" in f for f in result.failures)


def test_grade_must_not_match_violation() -> None:
    task = _task(must_not_match=[r"eval\("])
    reply = "```python\nx = eval('1')\n```"
    result = grade(reply, task)
    assert not result.passed
    assert any("must_not_match" in f for f in result.failures)


def test_grader_never_executes_code(tmp_path: Path) -> None:
    """NEG: grading code with a file-writing side effect must not create the
    file — proves no exec/eval/import of model output."""
    marker = tmp_path / "marker.txt"
    task = _task(must_match=[r"open\("])
    reply = f"```python\nopen({str(marker)!r}, 'w').write('x')\n```"

    grade(reply, task)

    assert not marker.exists()


def test_unfenced_reply_still_graded() -> None:
    """NEG: a fence-only extractor would grade '' for unfenced replies and
    silently fail every task for the wrong reason."""
    task = _task(must_match=[r"def f\("])
    reply = "def f():\n    return 1"
    result = grade(reply, task)
    assert result.passed
