"""Gold dataset loader and gold-ref resolver.

Gold datasets are committed JSON files (e.g. tests/evals/datasets/hermetic.json).
A ref identifies chunks by (source_path, heading_path) — never by raw chunk id,
since chunk ids shift when a pack is rebuilt.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tests.evals.eval_errors import EvalDatasetError

if TYPE_CHECKING:
    from synd.storage.db import Database

_VALID_DIFFICULTIES = frozenset({"direct", "paraphrase", "vocabulary_mismatch"})


@dataclass
class GoldRef:
    source_path: str
    heading_path: str


@dataclass
class GoldQuestion:
    id: str
    difficulty: str
    query: str
    keyword_query: str
    gold: list[GoldRef]


@dataclass
class EvalDataset:
    schema_version: int
    corpus_type: str
    package: str
    version: str | None
    questions: list[GoldQuestion]


def _require(obj: dict[str, Any], field: str, question_id: str, path: Path) -> Any:
    if field not in obj:
        raise EvalDatasetError(
            f"{path}: question {question_id!r} missing required field {field!r}"
        )
    return obj[field]


def _load_gold_ref(raw: dict[str, Any], question_id: str, path: Path) -> GoldRef:
    source_path = _require(raw, "source_path", question_id, path)
    heading_path = _require(raw, "heading_path", question_id, path)
    return GoldRef(source_path=source_path, heading_path=heading_path)


def _load_question(raw: dict[str, Any], path: Path) -> GoldQuestion:
    q_id = _require(raw, "id", "<unknown>", path)
    difficulty = _require(raw, "difficulty", q_id, path)
    if difficulty not in _VALID_DIFFICULTIES:
        raise EvalDatasetError(
            f"{path}: question {q_id!r} has invalid difficulty {difficulty!r} "
            f"(expected one of {sorted(_VALID_DIFFICULTIES)})"
        )
    query = _require(raw, "query", q_id, path)
    keyword_query = _require(raw, "keyword_query", q_id, path)
    gold_raw = _require(raw, "gold", q_id, path)
    if not gold_raw:
        raise EvalDatasetError(f"{path}: question {q_id!r} has an empty gold list")
    gold = [_load_gold_ref(g, q_id, path) for g in gold_raw]
    return GoldQuestion(
        id=q_id,
        difficulty=difficulty,
        query=query,
        keyword_query=keyword_query,
        gold=gold,
    )


def load_dataset(path: Path) -> EvalDataset:
    """Parse a gold dataset JSON file into typed dataclasses.

    Raises EvalDatasetError for a missing file, invalid JSON, a missing
    required field, an invalid difficulty value, or duplicate question ids.
    """
    if not path.exists():
        raise EvalDatasetError(f"dataset file not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EvalDatasetError(f"{path}: invalid JSON: {exc}") from exc

    schema_version = _require(raw, "schema_version", "<dataset>", path)
    corpus = _require(raw, "corpus", "<dataset>", path)
    questions_raw = _require(raw, "questions", "<dataset>", path)

    questions: list[GoldQuestion] = []
    seen_ids: set[str] = set()
    for q_raw in questions_raw:
        question = _load_question(q_raw, path)
        if question.id in seen_ids:
            raise EvalDatasetError(f"{path}: duplicate question id {question.id!r}")
        seen_ids.add(question.id)
        questions.append(question)

    return EvalDataset(
        schema_version=schema_version,
        corpus_type=corpus["type"],
        package=corpus["package"],
        version=corpus.get("version"),
        questions=questions,
    )


def resolve_gold_refs(db: "Database", package: str, refs: list[GoldRef]) -> set[int]:
    """Map gold refs to the union of matching chunk ids.

    A chunk matches a ref when chunk.source_url.endswith(ref.source_path) AND
    chunk.heading_path == ref.heading_path. A single ref may match multiple
    chunks (a long section split across chunks shares one heading_path) — all
    matches are relevant. A ref matching zero chunks raises EvalDatasetError
    naming the ref — this is the dataset-rot guard; silently skipping would
    shrink the gold set and inflate every downstream metric.
    """
    matched: set[int] = set()
    for ref in refs:
        rows = db.conn.execute(
            "SELECT id, source_url FROM chunks WHERE package = ? AND heading_path = ?",
            (package, ref.heading_path),
        ).fetchall()
        ref_matches = {
            row["id"]
            for row in rows
            if (row["source_url"] or "").endswith(ref.source_path)
        }
        if not ref_matches:
            raise EvalDatasetError(
                f"gold ref unresolvable: source_path={ref.source_path!r} "
                f"heading_path={ref.heading_path!r} (package={package!r})"
            )
        matched |= ref_matches
    return matched
