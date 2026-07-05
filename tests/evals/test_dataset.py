from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from synd.storage.db import Database
from synd.storage.models import Chunk, Pack, Page
from tests.evals.eval_errors import EvalDatasetError
from tests.evals.dataset import GoldRef, load_dataset, resolve_gold_refs

_HERMETIC_PATH = Path("tests/evals/datasets/hermetic.json")


def _make_db() -> Database:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        db = Database(db_path)
        db.create_schema()
        return db


def _pack(name: str = "docs") -> Pack:
    return Pack(
        name=name,
        version="1.0.0",
        lifecycle_state="approved",
        doc_version_status="stable",
        indexed_at="2026-01-01T00:00:00Z",
    )


def test_load_committed_hermetic_dataset() -> None:
    dataset = load_dataset(_HERMETIC_PATH)
    assert len(dataset.questions) == 42
    assert dataset.schema_version == 1
    assert dataset.package == "evalcorpus"


def test_load_missing_file_raises() -> None:
    with pytest.raises(EvalDatasetError):
        load_dataset(Path("tests/evals/datasets/does_not_exist.json"))


def test_load_invalid_json_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(EvalDatasetError):
        load_dataset(bad)


def test_load_missing_required_field_raises(tmp_path: Path) -> None:
    path = tmp_path / "missing_field.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "corpus": {"type": "hermetic", "package": "evalcorpus"},
                "questions": [
                    {
                        "id": "q1",
                        "difficulty": "direct",
                        "query": "x",
                        # keyword_query missing
                        "gold": [{"source_path": "a.md", "heading_path": "A"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(EvalDatasetError):
        load_dataset(path)


def test_load_duplicate_question_ids_raises(tmp_path: Path) -> None:
    path = tmp_path / "dup_ids.json"
    question = {
        "id": "q1",
        "difficulty": "direct",
        "query": "x",
        "keyword_query": "x",
        "gold": [{"source_path": "a.md", "heading_path": "A"}],
    }
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "corpus": {"type": "hermetic", "package": "evalcorpus"},
                "questions": [question, question],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(EvalDatasetError):
        load_dataset(path)


def test_resolve_refs_returns_all_matching_chunk_ids() -> None:
    db = _make_db()
    pack = _pack()
    pages = [Page(id=1, package="docs", version="1.0.0", url="index.md")]
    chunks = [
        Chunk(
            id=1,
            package="docs",
            version="1.0.0",
            page_id=1,
            heading_path="mcp/tools / Tools",
            content="content one",
            source_url="corpus/mcp/tools.md",
        ),
        Chunk(
            id=2,
            package="docs",
            version="1.0.0",
            page_id=1,
            heading_path="mcp/tools / Tools",
            content="content two",
            source_url="corpus/mcp/tools.md",
        ),
    ]
    db.import_pack(pack, pages, chunks)

    ref = GoldRef(source_path="mcp/tools.md", heading_path="mcp/tools / Tools")
    result = resolve_gold_refs(db, "docs", [ref])
    assert result == {1, 2}


def test_resolve_multiple_chunks_per_ref() -> None:
    """A ref may match a section split into several chunks — all are relevant."""
    db = _make_db()
    pack = _pack()
    pages = [Page(id=1, package="docs", version="1.0.0", url="index.md")]
    chunks = [
        Chunk(
            id=1,
            package="docs",
            version="1.0.0",
            page_id=1,
            heading_path="Guide / Setup",
            content="part one",
            source_url="corpus/guide.md",
        ),
        Chunk(
            id=2,
            package="docs",
            version="1.0.0",
            page_id=1,
            heading_path="Guide / Setup",
            content="part two",
            source_url="corpus/guide.md",
        ),
        Chunk(
            id=3,
            package="docs",
            version="1.0.0",
            page_id=1,
            heading_path="Guide / Other",
            content="unrelated",
            source_url="corpus/guide.md",
        ),
    ]
    db.import_pack(pack, pages, chunks)

    ref = GoldRef(source_path="guide.md", heading_path="Guide / Setup")
    result = resolve_gold_refs(db, "docs", [ref])
    assert result == {1, 2}


def test_unresolvable_ref_raises_not_skips() -> None:
    """NEG: a ref matching zero chunks must raise, never return a smaller set."""
    db = _make_db()
    pack = _pack()
    pages = [Page(id=1, package="docs", version="1.0.0", url="index.md")]
    chunks = [
        Chunk(
            id=1,
            package="docs",
            version="1.0.0",
            page_id=1,
            heading_path="mcp/tools / Tools",
            content="content",
            source_url="corpus/mcp/tools.md",
        ),
    ]
    db.import_pack(pack, pages, chunks)

    good_ref = GoldRef(source_path="mcp/tools.md", heading_path="mcp/tools / Tools")
    bad_ref = GoldRef(source_path="mcp/tools.md", heading_path="nope / nope")
    with pytest.raises(EvalDatasetError, match="nope / nope"):
        resolve_gold_refs(db, "docs", [good_ref, bad_ref])


def test_underscore_in_source_path_is_literal() -> None:
    """NEG: source_path 'a_b.md' must not match source_url ending in 'axb.md'
    (no SQL LIKE wildcard semantics — '_' means literal underscore)."""
    db = _make_db()
    pack = _pack()
    pages = [Page(id=1, package="docs", version="1.0.0", url="index.md")]
    chunks = [
        Chunk(
            id=1,
            package="docs",
            version="1.0.0",
            page_id=1,
            heading_path="Section",
            content="content",
            source_url="corpus/axb.md",
        ),
    ]
    db.import_pack(pack, pages, chunks)

    ref = GoldRef(source_path="a_b.md", heading_path="Section")
    with pytest.raises(EvalDatasetError):
        resolve_gold_refs(db, "docs", [ref])
