from pathlib import Path
import tempfile

import pytest

from synd.errors import SearchError
from synd.search.fts import search, search_relaxed, get_chunks_by_id, _preprocess_query
from synd.storage.db import Database
from synd.storage.models import Chunk, Page, Pack


def _make_db() -> Database:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        db = Database(db_path)
        db.create_schema()
        return db


def _import_pack(
    db: Database, pack: Pack, pages: list[Page], chunks: list[Chunk]
) -> None:
    db.import_pack(pack, pages, chunks)


def test_search_returns_ranked_results() -> None:
    db = _make_db()
    pack = Pack(
        name="docs",
        version="1.0.0",
        lifecycle_state="approved",
        doc_version_status="stable",
        indexed_at="2026-01-01T00:00:00Z",
    )
    pages = [Page(id=1, package="docs", version="1.0.0", url="index.md")]
    chunks = [
        Chunk(
            id=1,
            package="docs",
            version="1.0.0",
            page_id=1,
            heading_path="Introduction",
            summary="Welcome to docs",
            content="Welcome to the documentation",
            source_url="index.md",
        ),
    ]
    _import_pack(db, pack, pages, chunks)

    results = search(db, "Welcome")
    assert len(results) == 1
    assert results[0].heading_path == "Introduction"
    assert results[0].score > 0


def test_search_excludes_revoked() -> None:
    db = _make_db()
    pack_approved = Pack(
        name="docs",
        version="1.0.0",
        lifecycle_state="approved",
        doc_version_status="stable",
        indexed_at="2026-01-01T00:00:00Z",
    )
    pack_revoked = Pack(
        name="other",
        version="1.0.0",
        lifecycle_state="revoked",
        doc_version_status="stable",
        indexed_at="2026-01-01T00:00:00Z",
    )
    approved_pages = [Page(id=1, package="docs", version="1.0.0", url="index.md")]
    approved_chunks = [
        Chunk(
            id=1,
            package="docs",
            version="1.0.0",
            page_id=1,
            heading_path="Intro",
            summary="Welcome",
            content="Welcome to docs",
            source_url="index.md",
        ),
    ]
    revoked_pages = [Page(id=2, package="other", version="1.0.0", url="index.md")]
    revoked_chunks = [
        Chunk(
            id=2,
            package="other",
            version="1.0.0",
            page_id=2,
            heading_path="Intro",
            summary="Other intro",
            content="This is the other package",
            source_url="index.md",
        ),
    ]
    _import_pack(db, pack_approved, approved_pages, approved_chunks)
    _import_pack(db, pack_revoked, revoked_pages, revoked_chunks)

    results = search(db, "Welcome")
    assert len(results) == 1
    assert results[0].package == "docs"

    results_other = search(db, "other")
    assert len(results_other) == 0


def test_search_filters_by_package() -> None:
    db = _make_db()
    pack_a = Pack(
        name="alpha",
        version="1.0.0",
        lifecycle_state="approved",
        doc_version_status="stable",
        indexed_at="2026-01-01T00:00:00Z",
    )
    pack_b = Pack(
        name="beta",
        version="1.0.0",
        lifecycle_state="approved",
        doc_version_status="stable",
        indexed_at="2026-01-01T00:00:00Z",
    )
    pages_a = [Page(id=1, package="alpha", version="1.0.0", url="a.md")]
    chunks_a = [
        Chunk(
            id=1,
            package="alpha",
            version="1.0.0",
            page_id=1,
            heading_path="X",
            summary="Alpha content",
            content="Alpha package content here",
            source_url="a.md",
        ),
    ]
    pages_b = [Page(id=2, package="beta", version="1.0.0", url="b.md")]
    chunks_b = [
        Chunk(
            id=2,
            package="beta",
            version="1.0.0",
            page_id=2,
            heading_path="Y",
            summary="Beta content",
            content="Beta package content here",
            source_url="b.md",
        ),
    ]
    _import_pack(db, pack_a, pages_a, chunks_a)
    _import_pack(db, pack_b, pages_b, chunks_b)

    results = search(db, "content", packages=["beta"])
    assert len(results) == 1
    assert results[0].package == "beta"

    results_all = search(db, "content")
    assert len(results_all) == 2


def test_search_summary_mode_excludes_content() -> None:
    db = _make_db()
    pack = Pack(
        name="docs",
        version="1.0.0",
        lifecycle_state="approved",
        doc_version_status="stable",
        indexed_at="2026-01-01T00:00:00Z",
    )
    pages = [Page(id=1, package="docs", version="1.0.0", url="index.md")]
    chunks = [
        Chunk(
            id=1,
            package="docs",
            version="1.0.0",
            page_id=1,
            heading_path="Intro",
            summary="Welcome",
            content="Some long content text",
            source_url="index.md",
        ),
    ]
    _import_pack(db, pack, pages, chunks)

    results = search(db, "Welcome", detail="summary")
    assert len(results) == 1
    assert results[0].content is None


def test_search_full_mode_includes_content() -> None:
    db = _make_db()
    pack = Pack(
        name="docs",
        version="1.0.0",
        lifecycle_state="approved",
        doc_version_status="stable",
        indexed_at="2026-01-01T00:00:00Z",
    )
    pages = [Page(id=1, package="docs", version="1.0.0", url="index.md")]
    chunks = [
        Chunk(
            id=1,
            package="docs",
            version="1.0.0",
            page_id=1,
            heading_path="Intro",
            summary="Welcome",
            content="Some long content text",
            source_url="index.md",
        ),
    ]
    _import_pack(db, pack, pages, chunks)

    results = search(db, "Welcome", detail="full")
    assert len(results) == 1
    assert results[0].content == "Some long content text"


def test_search_deprecated_has_warning() -> None:
    db = _make_db()
    pack = Pack(
        name="docs",
        version="1.0.0",
        lifecycle_state="deprecated",
        doc_version_status="stable",
        indexed_at="2026-01-01T00:00:00Z",
    )
    pages = [Page(id=1, package="docs", version="1.0.0", url="index.md")]
    chunks = [
        Chunk(
            id=1,
            package="docs",
            version="1.0.0",
            page_id=1,
            heading_path="Intro",
            summary="Deprecated docs",
            content="This documentation is deprecated",
            source_url="index.md",
        ),
    ]
    _import_pack(db, pack, pages, chunks)

    results = search(db, "deprecated")
    assert len(results) == 1
    assert results[0].lifecycle_state == "deprecated"
    assert results[0].lifecycle_warning is not None
    assert "deprecated" in results[0].lifecycle_warning.lower()


def test_get_chunks_by_id() -> None:
    db = _make_db()
    pack = Pack(
        name="docs",
        version="1.0.0",
        lifecycle_state="approved",
        doc_version_status="stable",
        indexed_at="2026-01-01T00:00:00Z",
    )
    pages = [Page(id=1, package="docs", version="1.0.0", url="index.md")]
    chunks = [
        Chunk(
            id=1,
            package="docs",
            version="1.0.0",
            page_id=1,
            heading_path="A",
            summary="First",
            content="Content A",
            source_url="a.md",
        ),
        Chunk(
            id=2,
            package="docs",
            version="1.0.0",
            page_id=1,
            heading_path="B",
            summary="Second",
            content="Content B",
            source_url="b.md",
        ),
    ]
    _import_pack(db, pack, pages, chunks)

    results = get_chunks_by_id(db, [1, 2])
    assert len(results) == 2
    assert results[0].chunk_id == 1
    assert results[0].content == "Content A"
    assert results[1].chunk_id == 2
    assert results[1].content == "Content B"


def test_search_empty_index() -> None:
    db = _make_db()
    results = search(db, "anything")
    assert results == []


def test_search_no_match() -> None:
    db = _make_db()
    pack = Pack(
        name="docs",
        version="1.0.0",
        lifecycle_state="approved",
        doc_version_status="stable",
        indexed_at="2026-01-01T00:00:00Z",
    )
    pages = [Page(id=1, package="docs", version="1.0.0", url="index.md")]
    chunks = [
        Chunk(
            id=1,
            package="docs",
            version="1.0.0",
            page_id=1,
            heading_path="Intro",
            summary="Welcome",
            content="Welcome to docs",
            source_url="index.md",
        ),
    ]
    _import_pack(db, pack, pages, chunks)

    results = search(db, "zzzznonexistent")
    assert results == []


def test_search_malformed_query_raises_search_error() -> None:
    db = _make_db()
    with pytest.raises(SearchError):
        search(db, "foo AND")  # incomplete binary operator — invalid FTS5 syntax


def test_search_stopword_only_query_raises_search_error() -> None:
    """A query that reduces to nothing after stopword filtering raises SearchError."""
    db = _make_db()
    with pytest.raises(SearchError, match="common words"):
        search(db, "the is a")


def test_search_empty_string_returns_empty_list() -> None:
    """Truly empty input returns [] without raising — caller's responsibility."""
    db = _make_db()
    assert search(db, "") == []


def test_search_dot_in_query_does_not_raise() -> None:
    """'mcp.tool' must not crash — dot is an FTS5 syntax error without sanitization."""
    db = _make_db()
    results = search(db, "mcp.tool")
    assert results == []


def test_search_parens_in_query_do_not_raise() -> None:
    db = _make_db()
    results = search(db, "foo(bar)")
    assert results == []


def test_search_special_chars_stripped_still_matches() -> None:
    """Tokens survive sanitization and still match indexed content."""
    db = _make_db()
    pack = Pack(
        name="docs",
        version="1.0.0",
        lifecycle_state="approved",
        doc_version_status="stable",
        indexed_at="2026-01-01T00:00:00Z",
    )
    pages = [Page(id=1, package="docs", version="1.0.0", url="index.md")]
    chunks = [
        Chunk(
            id=1,
            package="docs",
            version="1.0.0",
            page_id=1,
            heading_path="Tools",
            summary="Tool usage",
            content="Use the mcp tool decorator to register functions",
            source_url="index.md",
        ),
    ]
    _import_pack(db, pack, pages, chunks)

    results = search(db, "mcp.tool")
    assert len(results) == 1
    assert results[0].heading_path == "Tools"


def test_search_query_of_only_special_chars_returns_empty() -> None:
    db = _make_db()
    results = search(db, "...")
    assert results == []


def test_search_best_match_first() -> None:
    db = _make_db()
    pack = Pack(
        name="docs",
        version="1.0.0",
        lifecycle_state="approved",
        doc_version_status="stable",
        indexed_at="2026-01-01T00:00:00Z",
    )
    pages = [Page(id=1, package="docs", version="1.0.0", url="index.md")]
    chunks = [
        Chunk(
            id=1,
            package="docs",
            version="1.0.0",
            page_id=1,
            heading_path="Python Guide",
            summary="Python programming",
            content="Python is great. Python tutorials. Python examples. Python best practices. Python syntax. Python functions. Python classes.",
            source_url="index.md",
        ),
        Chunk(
            id=2,
            package="docs",
            version="1.0.0",
            page_id=1,
            heading_path="Languages",
            summary="Overview",
            content="We support Java Go Rust Ruby and Python among others.",
            source_url="index.md",
        ),
    ]
    _import_pack(db, pack, pages, chunks)

    results = search(db, "python")
    assert len(results) == 2
    assert results[0].chunk_id == 1


def test_preprocess_query_filters_stopwords() -> None:
    """Common function words are stripped, leaving only meaningful terms."""
    assert _preprocess_query("install the package") == "install package"
    assert (
        _preprocess_query("is the authentication configured")
        == "authentication configured"
    )
    assert _preprocess_query("a token for the api") == "token api"


def test_preprocess_query_all_stopwords_returns_empty() -> None:
    """When every token is a stopword an empty string is returned so search() short-circuits."""
    assert _preprocess_query("the is a") == ""
    assert _preprocess_query("the") == ""


def test_preprocess_query_preserves_fts5_operators() -> None:
    """Uppercase AND / OR / NOT pass through — they are valid FTS5 operators."""
    assert _preprocess_query("foo AND bar") == "foo AND bar"
    assert _preprocess_query("foo OR bar") == "foo OR bar"


def test_search_heading_path_weighted_higher() -> None:
    """heading_path matches boost rank above summary-only matches (2.5x vs 1.5x weight)."""
    db = _make_db()
    pack = Pack(
        name="docs",
        version="1.0.0",
        lifecycle_state="approved",
        doc_version_status="stable",
        indexed_at="2026-01-01T00:00:00Z",
    )
    pages = [Page(id=1, package="docs", version="1.0.0", url="index.md")]
    chunks = [
        Chunk(
            id=1,
            package="docs",
            version="1.0.0",
            page_id=1,
            # heading_path does NOT contain the query term
            heading_path="General Overview",
            summary="oauth token authentication",
            content="Details about the system.",
            source_url="index.md",
        ),
        Chunk(
            id=2,
            package="docs",
            version="1.0.0",
            page_id=1,
            # heading_path ALSO contains the query term — should rank higher
            heading_path="oauth authentication flow",
            summary="oauth token authentication",
            content="Details about the system.",
            source_url="index.md",
        ),
    ]
    _import_pack(db, pack, pages, chunks)

    results = search(db, "oauth")
    assert len(results) == 2
    # Chunk 2 matches in both heading_path (2.5x weight) and summary (1.5x weight);
    # chunk 1 matches only in summary (1.5x weight). Chunk 2 must rank first.
    assert results[0].chunk_id == 2


# ---------------------------------------------------------------------------
# search_relaxed() tests
# ---------------------------------------------------------------------------


def _make_relaxed_db() -> Database:
    """DB with two chunks that do NOT share any term except 'progress'."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "relaxed.db"
        db = Database(db_path)
        db.create_schema()
        return db


def _pack_pages_chunks(
    pkg: str,
) -> tuple[Pack, list[Page], list[Chunk]]:
    pack = Pack(
        name=pkg,
        version="1.0.0",
        lifecycle_state="approved",
        doc_version_status="stable",
        indexed_at="2026-01-01T00:00:00Z",
    )
    pages = [Page(id=1, package=pkg, version="1.0.0", url="index.md")]
    chunks = [
        Chunk(
            id=1,
            package=pkg,
            version="1.0.0",
            page_id=1,
            heading_path="Progress Reporting",
            summary="Call report_progress to update the client",
            content="Long-running tools should call report_progress periodically",
            source_url="index.md",
        )
    ]
    return pack, pages, chunks


def test_search_relaxed_returns_results_on_first_try() -> None:
    """When the original query matches, returns (results, preprocessed_query)."""
    db = _make_relaxed_db()
    pack, pages, chunks = _pack_pages_chunks("docs")
    db.import_pack(pack, pages, chunks)

    results, effective = search_relaxed(db, "progress")
    assert len(results) == 1
    assert effective == "progress"


def test_search_relaxed_matches_on_partial_term_overlap() -> None:
    """A term absent from the corpus doesn't zero out an otherwise-good match.

    Under the old implicit-AND behavior this returned nothing until the
    unmatched term was dropped by relaxation. Under OR+BM25 there is no
    zero-result cliff: the chunk matching 'progress' alone is still a
    candidate, and is returned directly.
    """
    db = _make_relaxed_db()
    pack, pages, chunks = _pack_pages_chunks("docs")
    db.import_pack(pack, pages, chunks)

    # "progress xyznotexistent" — 'xyznotexistent' does not exist anywhere
    results, effective = search_relaxed(db, "progress xyznotexistent")
    assert len(results) == 1
    assert effective == "progress OR xyznotexistent"
    assert results[0].heading_path == "Progress Reporting"


def test_search_relaxed_all_terms_preserved_in_effective_query() -> None:
    """OR-joins every preprocessed term — none are dropped, matching or not."""
    db = _make_relaxed_db()
    pack, pages, chunks = _pack_pages_chunks("docs")
    db.import_pack(pack, pages, chunks)

    results, effective = search_relaxed(db, "progress missing1 missing2 missing3")
    assert len(results) == 1
    assert effective == "progress OR missing1 OR missing2 OR missing3"


def test_search_relaxed_returns_empty_when_nothing_matches() -> None:
    """When no term matches anywhere in the corpus, returns []."""
    db = _make_relaxed_db()
    pack, pages, chunks = _pack_pages_chunks("docs")
    db.import_pack(pack, pages, chunks)

    results, effective = search_relaxed(db, "zzznomatch anotherterm")
    assert results == []
    assert effective == "zzznomatch OR anotherterm"


def test_search_relaxed_multi_term_or_joined() -> None:
    """Both terms present → effective_query is the OR-joined preprocessed form."""
    db = _make_relaxed_db()
    pack, pages, chunks = _pack_pages_chunks("docs")
    db.import_pack(pack, pages, chunks)

    # both 'progress' and 'report_progress' appear in the chunk content
    results, effective = search_relaxed(db, "progress report_progress")
    assert len(results) == 1
    assert effective == "progress OR report_progress"


def test_search_relaxed_ranks_full_match_above_partial_match() -> None:
    """A chunk matching every query term outranks one matching only some,
    even though OR makes both candidates — this is the actual behavior the
    AND->OR change depends on: broadening the candidate pool must not come
    at the cost of burying the best match.
    """
    db = _make_relaxed_db()
    pack = Pack(
        name="docs",
        version="1.0.0",
        lifecycle_state="approved",
        doc_version_status="stable",
        indexed_at="2026-01-01T00:00:00Z",
    )
    pages = [Page(id=1, package="docs", version="1.0.0", url="index.md")]
    chunks = [
        Chunk(
            id=1,
            package="docs",
            version="1.0.0",
            page_id=1,
            heading_path="Full Match",
            summary="",
            content="alpha beta gamma",
            source_url="index.md",
        ),
        Chunk(
            id=2,
            package="docs",
            version="1.0.0",
            page_id=1,
            heading_path="Partial Match",
            summary="",
            content="alpha only, nothing else relevant here",
            source_url="index.md",
        ),
    ]
    db.import_pack(pack, pages, chunks)

    results, effective = search_relaxed(db, "alpha beta gamma")
    assert effective == "alpha OR beta OR gamma"
    assert len(results) == 2
    assert results[0].chunk_id == 1  # full match ranks first
    assert results[1].chunk_id == 2  # partial match still returned, ranked lower


def test_search_relaxed_stopwords_only_raises() -> None:
    """Stopwords-only input raises SearchError, same as search()."""
    db = _make_relaxed_db()
    with pytest.raises(SearchError, match="common words"):
        search_relaxed(db, "the is a")


def test_search_relaxed_empty_string_returns_empty() -> None:
    db = _make_relaxed_db()
    results, effective = search_relaxed(db, "")
    assert results == []


def test_search_relaxed_malformed_operator_raises() -> None:
    """Incomplete FTS5 boolean operator still raises SearchError."""
    db = _make_relaxed_db()
    with pytest.raises(SearchError):
        search_relaxed(db, "foo AND")


# -- porter stemming (D30 step 1) --


def _make_stemming_db() -> Database:
    """One pack, one chunk whose content uses only singular/base word forms."""
    db = _make_db()
    pack = Pack(
        name="mathdocs",
        version="1.0.0",
        lifecycle_state="approved",
        doc_version_status="stable",
        indexed_at="2026-01-01T00:00:00Z",
    )
    pages = [Page(id=1, package="mathdocs", version="1.0.0", url="mathtext.md")]
    chunks = [
        Chunk(
            id=1,
            package="mathdocs",
            version="1.0.0",
            page_id=1,
            heading_path="Mathtext",
            summary="Render a mathematical formula in a label",
            content=(
                "Use the internal parser to render a mathematical formula "
                "inside an axis label or title. The annotation is drawn with "
                "the layout engine."
            ),
            source_url="mathtext.md",
        ),
    ]
    _import_pack(db, pack, pages, chunks)
    return db


def test_search_bridges_plural_query_to_singular_content() -> None:
    """'formulas' must match a chunk that only ever says 'formula'.

    The html_v1 vocabulary_mismatch tier showed 4/26 questions failing purely
    on morphology (unicode61 does no stemming) — see decisions.md D30.
    """
    db = _make_stemming_db()
    results = search(db, "formulas")
    assert len(results) == 1
    assert results[0].heading_path == "Mathtext"


def test_search_bridges_singular_query_to_plural_content() -> None:
    """The reverse direction: 'label' content, 'labels' query."""
    db = _make_stemming_db()
    results = search(db, "labels")
    assert len(results) == 1


def test_search_bridges_inflected_verb_forms() -> None:
    """'rendering annotations' must match 'render ... annotation'."""
    db = _make_stemming_db()
    results = search(db, "rendering annotations")
    assert len(results) == 1


def test_search_relaxed_also_stems() -> None:
    """search_relaxed shares the FTS index, so stemming applies there too."""
    db = _make_stemming_db()
    results, effective = search_relaxed(db, "formulas titles")
    assert len(results) == 1
    assert effective == "formulas OR titles"
