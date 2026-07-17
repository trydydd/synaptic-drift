"""Shared eval fixtures: builds the evalcorpus package once per test session."""

from __future__ import annotations

import json
import zipfile
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest

from synd.builder.build import build_pack
from synd.builder.manifest import load_manifest
from synd.storage.db import Database
from synd.storage.models import Chunk, Page, Pack

EVAL_CORPUS_DIR = Path(__file__).parent / "fixtures" / "corpus"
EVAL_PACKAGE = "evalcorpus"
EVAL_VERSION = "1.0.0"


def load_ctx_into_db(ctx_path: Path, db: Database) -> int:
    """Import a .ctx pack into db. Returns the number of chunks imported."""
    manifest = load_manifest(ctx_path)
    pack = Pack(
        name=str(manifest["package"]),
        version=str(manifest["version"]),
        lifecycle_state=str(manifest["lifecycle_state"]),
        doc_version_status=str(manifest.get("doc_version_status", "unknown")),
        indexed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        policy_profile=str(manifest.get("policy_profile", "")),
        pack_digest=str(manifest["pack_digest"]),
        normalized_content_hash=str(manifest["normalized_content_hash"]),
        source_url=str(manifest.get("source_url", "")),
        source_commit=str(manifest.get("source_commit", "")),
        owner=str(manifest.get("owner", "")),
        pack_source=str(ctx_path),
    )

    with zipfile.ZipFile(ctx_path, "r") as zf:
        pages_data = json.loads(zf.read("pages.json"))
        chunks_data = zf.read("chunks.jsonl").decode("utf-8")

    pages = [
        Page(
            id=p["id"],
            package=p["package"],
            version=p["version"],
            url=p["url"],
            title=p.get("title"),
            content_hash=p.get("content_hash"),
        )
        for p in pages_data
    ]

    chunks = []
    for line in chunks_data.strip().split("\n"):
        if not line:
            continue
        c = json.loads(line)
        chunks.append(
            Chunk(
                id=c["id"],
                package=pack.name,
                version=pack.version,
                content=c["content"],
                page_id=c.get("page_id"),
                heading_path=c.get("heading_path"),
                summary=c.get("summary"),
                token_count=c.get("token_count"),
                source_url=c.get("source_url"),
                source_commit=c.get("source_commit"),
                content_hash=c.get("content_hash"),
            )
        )

    db.import_pack(pack, pages, chunks)
    return len(chunks)


@pytest.fixture(scope="session")
def eval_db(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Database]:
    """Session-scoped Database containing the evalcorpus pack. Built once."""
    build_dir = tmp_path_factory.mktemp("eval_corpus_build")
    ctx_path, _ = build_pack(
        package=EVAL_PACKAGE,
        version=EVAL_VERSION,
        source=EVAL_CORPUS_DIR,
        output=build_dir,
    )

    db_dir = tmp_path_factory.mktemp("eval_db")
    db = Database(db_dir / "eval.db")
    db.create_schema()
    load_ctx_into_db(ctx_path, db)
    try:
        yield db
    finally:
        db.close()
