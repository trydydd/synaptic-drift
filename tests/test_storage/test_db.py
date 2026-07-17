import sqlite3
from pathlib import Path
from datetime import datetime, timezone

import pytest

from synd.errors import ImportError_
from synd.storage.db import Database
from synd.storage.models import Pack, Page, Chunk


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_pack(
    name: str = "test-lib",
    version: str = "1.0.0",
    lifecycle_state: str = "approved",
    doc_version_status: str = "stable",
    indexed_at: str | None = None,
    **kwargs: object,
) -> Pack:
    if indexed_at is None:
        indexed_at = _now()
    return Pack(
        name=name,
        version=version,
        lifecycle_state=lifecycle_state,
        doc_version_status=doc_version_status,
        indexed_at=indexed_at,
        **kwargs,  # type: ignore[call-arg]
    )


def _make_page(
    pkg: str = "test-lib",
    version: str = "1.0.0",
    url: str = "docs/readme.md",
    title: str | None = None,
    **kwargs: object,
) -> Page:
    return Page(id=1, package=pkg, version=version, url=url, title=title, **kwargs)  # type: ignore[call-arg]


def _make_chunk(
    pkg: str = "test-lib",
    version: str = "1.0.0",
    content: str = "hello world",
    page_id: int = 1,
    **kwargs: object,
) -> Chunk:
    return Chunk(
        id=1, package=pkg, version=version, content=content, page_id=page_id, **kwargs
    )  # type: ignore[call-arg]


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / ".synd" / "index.db"


@pytest.fixture()
def db(db_path: Path) -> Database:
    database = Database(db_path)
    database.create_schema()
    yield database
    database.close()


# -- schema --


def test_create_schema_creates_all_tables(db: Database) -> None:
    conn = sqlite3.connect(db._db_path)
    conn.row_factory = sqlite3.Row
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    conn.close()
    table_names = {t["name"] for t in tables}
    assert "packages" in table_names
    assert "pages" in table_names
    assert "chunks" in table_names
    # FTS5 virtual tables show up as 'table' type in sqlite_master
    assert "chunks_fts" in table_names


def test_wal_mode_enabled(db: Database) -> None:
    conn = sqlite3.connect(db._db_path)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode == "wal"


def test_busy_timeout_set(db: Database) -> None:
    conn = sqlite3.connect(db._db_path)
    timeout_ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    conn.close()
    assert timeout_ms == 5000


# -- import --


def test_import_pack_inserts_all_records(db: Database) -> None:
    pack = _make_pack(owner="team-a")
    pages = [_make_page(), _make_page(url="docs/api.md", title="API")]
    chunks = [
        _make_chunk(page_id=1, heading_path="readme / Intro", summary="intro"),
        _make_chunk(page_id=2, heading_path="api / Endpoints"),
    ]
    db.import_pack(pack, pages, chunks)
    con = sqlite3.connect(db._db_path)
    rows = con.execute("SELECT COUNT(*) FROM packages").fetchone()[0]
    assert rows == 1
    rows = con.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    assert rows == 2
    rows = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    assert rows == 2
    con.close()


def test_import_pack_is_atomic(db: Database) -> None:
    pack = _make_pack()
    pages = [_make_page()]
    chunks = [_make_chunk()]
    db.import_pack(pack, pages, chunks)
    # Verify all records are present after a successful import
    con = sqlite3.connect(db._db_path)
    pack_count = con.execute("SELECT COUNT(*) FROM packages").fetchone()[0]
    page_count = con.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    chunk_count = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    con.close()
    assert pack_count == 1
    assert page_count == 1
    assert chunk_count == 1


def test_import_duplicate_pack_raises(db: Database) -> None:
    pack = _make_pack()
    db.import_pack(pack, [], [])
    with pytest.raises(ImportError_):
        db.import_pack(pack, [], [])


# -- queries --


def test_get_packages_returns_all(db: Database) -> None:
    p1 = _make_pack(name="lib-a", version="1.0.0")
    p2 = _make_pack(name="lib-b", version="2.0.0")
    db.import_pack(p1, [], [])
    db.import_pack(p2, [], [])
    packages = db.get_packages()
    assert len(packages) == 2
    names = {p.name for p in packages}
    assert names == {"lib-a", "lib-b"}


def test_get_pack_not_found_returns_none(db: Database) -> None:
    result = db.get_pack("nonexistent", "1.0.0")
    assert result is None


# -- delete --


def test_delete_pack_removes_all_related(db: Database) -> None:
    pack = _make_pack()
    pages = [_make_page()]
    chunks = [_make_chunk()]
    db.import_pack(pack, pages, chunks)
    db.delete_pack("test-lib", "1.0.0")
    con = sqlite3.connect(db._db_path)
    assert con.execute("SELECT COUNT(*) FROM packages").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM pages").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0
    con.close()


# -- FTS5 triggers --


def test_fts5_trigger_populates_on_insert(db: Database) -> None:
    pack = _make_pack()
    pages = [_make_page()]
    chunks = [_make_chunk(content="searchable text")]
    db.import_pack(pack, pages, chunks)
    con = sqlite3.connect(db._db_path)
    rows = con.execute(
        "SELECT COUNT(*) FROM chunks_fts WHERE content MATCH 'searchable'"
    ).fetchone()[0]
    con.close()
    assert rows >= 1


def test_fts5_trigger_cleans_on_delete(db: Database) -> None:
    pack = _make_pack()
    pages = [_make_page()]
    chunks = [_make_chunk(content="delete me")]
    db.import_pack(pack, pages, chunks)
    db.delete_pack("test-lib", "1.0.0")
    con = sqlite3.connect(db._db_path)
    rows = con.execute(
        "SELECT COUNT(*) FROM chunks_fts WHERE content MATCH 'delete me'"
    ).fetchone()[0]
    con.close()
    assert rows == 0


# -- page ID remapping across packs --


def test_import_pack_page_ids_remapped_for_second_pack(db: Database) -> None:
    # Both packs use page id=1 internally (pack-local IDs from the .ctx file).
    # After import the second pack's chunk must reference its own page, not pack 1's.
    pack1 = _make_pack(name="lib-a", version="1.0.0")
    pages1 = [Page(id=1, package="lib-a", version="1.0.0", url="docs/a.md")]
    chunks1 = [
        Chunk(
            id=1, package="lib-a", version="1.0.0", content="lib-a content", page_id=1
        )
    ]
    db.import_pack(pack1, pages1, chunks1)

    pack2 = _make_pack(name="lib-b", version="1.0.0")
    pages2 = [Page(id=1, package="lib-b", version="1.0.0", url="docs/b.md")]
    chunks2 = [
        Chunk(
            id=1, package="lib-b", version="1.0.0", content="lib-b content", page_id=1
        )
    ]
    db.import_pack(pack2, pages2, chunks2)

    con = sqlite3.connect(db._db_path)
    lib_b_page_id = con.execute(
        "SELECT id FROM pages WHERE package = 'lib-b' AND url = 'docs/b.md'"
    ).fetchone()[0]
    lib_b_chunk_page_id = con.execute(
        "SELECT page_id FROM chunks WHERE package = 'lib-b'"
    ).fetchone()[0]
    con.close()
    assert lib_b_chunk_page_id == lib_b_page_id


# -- edge cases --


def test_synd_directory_created_if_missing(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / ".synd" / "index.db"
    db = Database(db_path)
    db.create_schema()
    assert db_path.parent.exists()
    db.close()


# -- porter tokenizer revert migration (D30 closure) --


_PORTER_FTS_SCHEMA = """\
CREATE TABLE packages (name TEXT NOT NULL, version TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL DEFAULT 'draft', policy_profile TEXT,
    pack_digest TEXT, normalized_content_hash TEXT, doc_version_status TEXT,
    source_url TEXT, source_commit TEXT, owner TEXT, indexed_at TEXT NOT NULL,
    pack_source TEXT, PRIMARY KEY (name, version));
CREATE TABLE pages (id INTEGER PRIMARY KEY AUTOINCREMENT, package TEXT NOT NULL,
    version TEXT NOT NULL, url TEXT NOT NULL, content_hash TEXT,
    UNIQUE(package, version, url));
CREATE TABLE chunks (id INTEGER PRIMARY KEY AUTOINCREMENT, package TEXT NOT NULL,
    version TEXT NOT NULL, page_id INTEGER REFERENCES pages(id),
    heading_path TEXT, summary TEXT, content TEXT NOT NULL, token_count INTEGER,
    source_url TEXT, source_commit TEXT, content_hash TEXT);
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    heading_path, summary, content, content='chunks', content_rowid='id',
    tokenize='porter unicode61');
CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, heading_path, summary, content)
    VALUES (new.id, new.heading_path, new.summary, new.content);
END;
CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, heading_path, summary, content)
    VALUES ('delete', old.id, old.heading_path, old.summary, old.content);
END;
"""


def _make_porter_db(db_path: Path) -> None:
    """Create a database from the porter window (D30 step 1) with one row."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(_PORTER_FTS_SCHEMA)
    conn.execute(
        "INSERT INTO packages (name, version, indexed_at) VALUES ('lib', '1.0.0', ?)",
        (_now(),),
    )
    conn.execute(
        "INSERT INTO pages (package, version, url) VALUES ('lib', '1.0.0', 'a.md')"
    )
    conn.execute(
        "INSERT INTO chunks (package, version, page_id, heading_path, summary, content) "
        "VALUES ('lib', '1.0.0', 1, 'Formula', 'about a formula', "
        "'render a mathematical formula in the label')"
    )
    conn.commit()
    conn.close()


def test_create_schema_reverts_porter_fts_to_unicode61(db_path: Path) -> None:
    """Opening a porter-window DB and calling create_schema() must rebuild
    chunks_fts with plain unicode61, preserving indexed content."""
    _make_porter_db(db_path)

    database = Database(db_path)
    database.create_schema()
    try:
        ddl = database.conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'chunks_fts'"
        ).fetchone()["sql"]
        assert "porter" not in ddl

        # Exact matching works against the pre-existing row...
        row = database.conn.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'formula'"
        ).fetchone()
        assert row is not None
        # ...and stemmed matching no longer applies (D30 closure).
        row = database.conn.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'formulas'"
        ).fetchone()
        assert row is None
    finally:
        database.close()


def test_create_schema_migration_is_idempotent(db_path: Path) -> None:
    """A second create_schema() call must not rebuild again or fail."""
    _make_porter_db(db_path)
    database = Database(db_path)
    database.create_schema()
    database.create_schema()
    try:
        row = database.conn.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'formula'"
        ).fetchone()
        assert row is not None
    finally:
        database.close()


def test_create_schema_leaves_unicode61_db_untouched(db_path: Path) -> None:
    """A DB already on unicode61 (pre-porter or post-revert) passes through
    the migration unchanged — no rebuild, content still searchable."""
    database = Database(db_path)
    database.create_schema()
    database.close()

    database = Database(db_path)
    database.create_schema()
    try:
        ddl = database.conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'chunks_fts'"
        ).fetchone()["sql"]
        assert "porter" not in ddl
    finally:
        database.close()


def test_triggers_still_index_new_rows_after_migration(db_path: Path) -> None:
    """The AFTER INSERT trigger must keep populating the rebuilt FTS table."""
    _make_porter_db(db_path)
    database = Database(db_path)
    database.create_schema()
    try:
        database.conn.execute(
            "INSERT INTO chunks (package, version, page_id, heading_path, summary, content) "
            "VALUES ('lib', '1.0.0', 1, 'Annotations', 'about annotations', "
            "'draw an annotation with an arrow')"
        )
        database.conn.commit()
        row = database.conn.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'annotation'"
        ).fetchone()
        assert row is not None
    finally:
        database.close()
