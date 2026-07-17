from __future__ import annotations

from pathlib import Path

import pytest

from synd.builder.build import build_pack
from synd.storage.db import Database
from tests.evals.conftest import (
    EVAL_CORPUS_DIR,
    EVAL_PACKAGE,
    EVAL_VERSION,
    load_ctx_into_db,
)

pytestmark = pytest.mark.evals


def test_eval_db_contains_corpus_pack(eval_db: Database) -> None:
    row = eval_db.conn.execute(
        "SELECT COUNT(*) AS cnt FROM packages WHERE name = ? AND version = ?",
        (EVAL_PACKAGE, EVAL_VERSION),
    ).fetchone()
    assert row["cnt"] == 1


def test_eval_db_chunk_count_above_floor(eval_db: Database) -> None:
    row = eval_db.conn.execute(
        "SELECT COUNT(*) AS cnt FROM chunks WHERE package = ?", (EVAL_PACKAGE,)
    ).fetchone()
    assert row["cnt"] > 50


def test_corpus_build_is_deterministic(tmp_path: Path) -> None:
    """NEG: building the corpus twice must yield identical chunk counts and
    an identical chunk-1 content_hash — otherwise gold refs resolve to
    different ids between runs and every downstream metric becomes noise."""
    ctx_path_a, _ = build_pack(
        package=EVAL_PACKAGE,
        version=EVAL_VERSION,
        source=EVAL_CORPUS_DIR,
        output=tmp_path / "a",
    )
    ctx_path_b, _ = build_pack(
        package=EVAL_PACKAGE,
        version=EVAL_VERSION,
        source=EVAL_CORPUS_DIR,
        output=tmp_path / "b",
    )

    db_a = Database(tmp_path / "a.db")
    db_a.create_schema()
    count_a = load_ctx_into_db(ctx_path_a, db_a)

    db_b = Database(tmp_path / "b.db")
    db_b.create_schema()
    count_b = load_ctx_into_db(ctx_path_b, db_b)

    assert count_a == count_b

    hash_a = db_a.conn.execute(
        "SELECT content_hash FROM chunks WHERE package = ? ORDER BY id LIMIT 1",
        (EVAL_PACKAGE,),
    ).fetchone()["content_hash"]
    hash_b = db_b.conn.execute(
        "SELECT content_hash FROM chunks WHERE package = ? ORDER BY id LIMIT 1",
        (EVAL_PACKAGE,),
    ).fetchone()["content_hash"]

    assert hash_a == hash_b

    db_a.close()
    db_b.close()
