import re
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from synd.errors import SearchError

if TYPE_CHECKING:
    from synd.storage.db import Database

_FTS5_SPECIAL_RE = re.compile(r"[^\w\s]")

# Uppercase FTS5 boolean operators pass through preprocessing unfiltered (see
# _preprocess_query). A query that already uses one is respected verbatim by
# search_relaxed rather than having its terms re-joined with OR.
_FTS5_OPERATORS = frozenset({"AND", "OR", "NOT"})

# Conservative set of English function words that carry no search value in
# technical documentation. Excludes words that can appear in section titles
# (how, what, where) and FTS5 boolean operators (AND, OR, NOT).
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "with",
        "by",
        "from",
        "as",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
    }
)


def _sanitize_query(query: str) -> str:
    """Strip FTS5 special characters and collapse whitespace."""
    return " ".join(_FTS5_SPECIAL_RE.sub(" ", query).split())


def _preprocess_query(query: str) -> str:
    """Sanitize and filter stopwords from a query.

    Returns the filtered query, or an empty string if all tokens are stopwords
    or the input contained no word characters.
    """
    sanitized = _sanitize_query(query)
    if not sanitized:
        return sanitized
    tokens = sanitized.split()
    filtered = [t for t in tokens if t.lower() not in _STOPWORDS]
    return " ".join(filtered)


@dataclass
class SearchResult:
    chunk_id: int
    package: str
    version: str
    heading_path: str | None
    summary: str | None
    content: (
        str | None
    )  # None when returned from search(); always populated from get_chunks_by_id()
    source_url: str | None
    source_commit: str | None
    content_hash: str | None
    lifecycle_state: str
    doc_version_status: str | None
    indexed_at: str
    score: float
    lifecycle_warning: str | None  # set when lifecycle_state == 'deprecated'


def _execute_fts(
    db: "Database",
    sanitized: str,
    packages: list[str] | None,
    detail: str,
    limit: int,
) -> list[SearchResult]:
    """Run the FTS5 query against the database and return results.

    Expects `sanitized` to already be preprocessed. Raises SearchError on
    SQLite errors (including malformed FTS5 syntax).
    """
    conn = db.conn
    try:
        pkg_where = ""
        params: list[Any] = []
        if packages:
            placeholders = ",".join("?" for _ in packages)
            pkg_where = f" AND p.name IN ({placeholders})"
            params.extend(packages)

        if detail == "full":
            select_cols = (
                "c.id, p.name, p.version, c.heading_path, c.summary, "
                "c.content, c.source_url, c.source_commit, c.content_hash, "
                "p.lifecycle_state, p.doc_version_status, p.indexed_at, "
            )
        else:
            select_cols = (
                "c.id, p.name, p.version, c.heading_path, c.summary, "
                "NULL, c.source_url, c.source_commit, c.content_hash, "
                "p.lifecycle_state, p.doc_version_status, p.indexed_at, "
            )

        sql = f"""\
SELECT {select_cols}
       -bm25(chunks_fts, 2.5, 1.5, 1.0) AS score
FROM chunks_fts, chunks c, packages p
WHERE chunks_fts.rowid = c.id
  AND c.package = p.name
  AND c.version = p.version
  AND p.lifecycle_state != 'revoked'{pkg_where}
  AND chunks_fts MATCH ?
ORDER BY score DESC
LIMIT ?"""

        params.append(sanitized)
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
    except sqlite3.Error as exc:
        raise SearchError(f"Full-text search failed: {exc}") from exc

    # SELECT columns: 0=id, 1=package, 2=version, 3=heading_path, 4=summary,
    # 5=content (NULL in summary mode), 6=source_url, 7=source_commit, 8=content_hash,
    # 9=lifecycle_state, 10=doc_version_status, 11=indexed_at, 12=score
    # BM25 weights: heading_path 2.5x, summary 1.5x, content 1.0x
    results: list[SearchResult] = []
    for row in rows:
        lifecycle_state = row[9]
        results.append(
            SearchResult(
                chunk_id=row[0],
                package=row[1],
                version=row[2],
                heading_path=row[3],
                summary=row[4],
                content=row[5],
                source_url=row[6],
                source_commit=row[7],
                content_hash=row[8],
                lifecycle_state=lifecycle_state,
                doc_version_status=row[10],
                indexed_at=row[11],
                score=row[12],
                lifecycle_warning="This package is deprecated"
                if lifecycle_state == "deprecated"
                else None,
            )
        )

    return results


def search(
    db: "Database",
    query: str,
    packages: list[str] | None = None,
    detail: str = "summary",
    limit: int = 10,
) -> list[SearchResult]:
    sanitized = _preprocess_query(query)
    if not sanitized:
        if _sanitize_query(query):
            raise SearchError(
                "Query contains only common words with no search value. "
                "Use more specific terms."
            )
        return []

    return _execute_fts(db, sanitized, packages, detail, limit)


def search_relaxed(
    db: "Database",
    query: str,
    packages: list[str] | None = None,
    detail: str = "summary",
    limit: int = 10,
) -> tuple[list[SearchResult], str]:
    """Like search(), but OR-matches terms and ranks by BM25 instead of
    requiring every term to co-occur (FTS5's implicit AND).

    Terms are joined with OR so a chunk matching more/rarer query terms scores
    higher, but a chunk matching only one term is still a candidate — there is
    no zero-result cliff from one term failing to co-occur with the rest, and
    therefore no retry-with-fewer-terms loop needed. Measured on the pilot L1
    eval (docs/pilot-results.md): this outperforms AND-with-relaxation on every
    recall/MRR/nDCG metric for both natural-language and keyword-style queries
    — including precision-sensitive ones (recall@1, MRR), so the gain is not
    just "return more candidates and hope." AND-first variants (try strict AND,
    fall back to OR only when it returns nothing, or only at the end of a
    term-dropping cascade) were also measured and are no better: whenever
    strict AND succeeds, its results already rank at the top of the OR
    ordering, so trying AND first adds complexity without improving results,
    and folding OR only into a term-dropping cascade reintroduces the original
    problem (the cascade already stops at the first non-empty, low-quality
    subset before ever reaching the OR step).

    If the query already contains explicit FTS5 boolean operators (AND/OR/NOT),
    it is passed through as the user wrote it — see _FTS5_OPERATORS — rather
    than having its terms re-joined with OR.

    Returns (results, effective_query) where effective_query is the exact
    string sent to FTS5's MATCH after preprocessing (and OR-joining, when
    applicable).

    Raises SearchError for stopwords-only input and malformed FTS5 syntax (e.g.
    an incomplete boolean operator like "foo AND").
    """
    preprocessed = _preprocess_query(query)
    if not preprocessed:
        if _sanitize_query(query):
            raise SearchError(
                "Query contains only common words with no search value. "
                "Use more specific terms."
            )
        return [], query

    tokens = preprocessed.split()
    effective = (
        preprocessed
        if any(t in _FTS5_OPERATORS for t in tokens)
        else " OR ".join(tokens)
    )
    results = _execute_fts(db, effective, packages, detail, limit)
    return results, effective


def get_chunks_by_id(
    db: "Database",
    chunk_ids: list[int],
    detail: str = "full",
) -> list[SearchResult]:
    if not chunk_ids:
        return []

    conn = db.conn

    placeholders = ",".join("?" for _ in chunk_ids)
    sql = (
        "SELECT c.id, p.name, p.version, c.heading_path, c.summary, c.content, "
        "c.source_url, c.source_commit, c.content_hash, "
        "p.lifecycle_state, p.doc_version_status, p.indexed_at "
        "FROM chunks c, packages p "
        "WHERE c.package = p.name AND c.version = p.version "
        "  AND c.id IN (" + placeholders + ") "
        "  AND p.lifecycle_state != 'revoked' "
        "ORDER BY c.id"
    )

    rows = conn.execute(sql, chunk_ids).fetchall()
    # SELECT columns: 0=id, 1=package, 2=version, 3=heading_path, 4=summary,
    # 5=content, 6=source_url, 7=source_commit, 8=content_hash,
    # 9=lifecycle_state, 10=doc_version_status, 11=indexed_at
    results: list[SearchResult] = []
    for row in rows:
        lifecycle_state = row[9]
        results.append(
            SearchResult(
                chunk_id=row[0],
                package=row[1],
                version=row[2],
                heading_path=row[3],
                summary=row[4],
                content=row[5] if detail == "full" else None,
                source_url=row[6],
                source_commit=row[7],
                content_hash=row[8],
                lifecycle_state=lifecycle_state,
                doc_version_status=row[10],
                indexed_at=row[11],
                score=0.0,
                lifecycle_warning="This package is deprecated"
                if lifecycle_state == "deprecated"
                else None,
            )
        )

    return results
