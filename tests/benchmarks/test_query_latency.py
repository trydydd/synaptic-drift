"""FTS5 query latency benchmark.

Generates a synthetic index of ~100,000 chunks and measures search() latency
across representative query types. Writes results to
tests/benchmarks/results/latency.json.

Run:
    pytest tests/benchmarks/test_query_latency.py -v -s

The P95 assertion (< 100 ms) is a safety-net regression guard with generous
headroom — actual observed latency on commodity hardware is in the low single
digits of milliseconds.
"""

from __future__ import annotations

import json
import random
import tempfile
import time
from pathlib import Path

import pytest

from synd.search.fts import search
from synd.storage.db import Database
from synd.storage.models import Chunk, Pack, Page

RESULTS_DIR = Path(__file__).parent / "results"
CHUNK_COUNT = 100_000
PAGES_PER_PACK = 1_000  # 100 chunks per page
REPS = 50

_VOCAB = [
    "install",
    "configure",
    "authentication",
    "token",
    "oauth",
    "jwt",
    "endpoint",
    "middleware",
    "plugin",
    "async",
    "await",
    "callback",
    "error",
    "exception",
    "handler",
    "router",
    "schema",
    "model",
    "database",
    "migration",
    "query",
    "index",
    "cache",
    "redis",
    "docker",
    "kubernetes",
    "deploy",
    "build",
    "release",
    "version",
    "python",
    "typescript",
    "javascript",
    "rust",
    "golang",
    "java",
    "api",
    "rest",
    "graphql",
    "grpc",
    "websocket",
    "http",
    "https",
    "request",
    "response",
    "header",
    "body",
    "status",
    "code",
    "class",
    "function",
    "method",
    "property",
    "interface",
    "type",
    "import",
    "export",
    "module",
    "package",
    "dependency",
    "lock",
    "test",
    "mock",
    "fixture",
    "assertion",
    "coverage",
    "lint",
    "config",
    "environment",
    "variable",
    "secret",
    "key",
    "certificate",
    "sigstore",
    "ed25519",
    "sha256",
    "digest",
    "manifest",
    "archive",
]

# Query types: (label, query_string, limit)
_QUERIES = [
    ("single_common_term", "install", 10),
    ("multi_term", "install package", 10),
    ("rare_term", "sigstore", 10),
    ("high_limit_common", "configuration", 20),
    ("mixed_terms", "authentication token", 10),
]


def _build_large_db(tmp_dir: Path) -> Database:
    rng = random.Random(42)
    db = Database(tmp_dir / "bench.db")
    db.create_schema()

    pack = Pack(
        name="benchmark",
        version="1.0.0",
        lifecycle_state="approved",
        doc_version_status="stable",
        indexed_at="2026-01-01T00:00:00Z",
    )

    chunks_per_page = CHUNK_COUNT // PAGES_PER_PACK
    pages = [
        Page(id=i, package="benchmark", version="1.0.0", url=f"docs/page{i}.md")
        for i in range(1, PAGES_PER_PACK + 1)
    ]

    chunks = []
    for chunk_id in range(1, CHUNK_COUNT + 1):
        page_id = ((chunk_id - 1) // chunks_per_page) + 1
        w = rng.choices(_VOCAB, k=4)
        chunks.append(
            Chunk(
                id=chunk_id,
                package="benchmark",
                version="1.0.0",
                page_id=page_id,
                heading_path=f"{w[0].capitalize()} / {w[1].capitalize()}",
                summary=f"{w[0].capitalize()} {w[1]} for {w[2]}",
                content=" ".join(rng.choices(_VOCAB, k=40)),
                source_url=f"docs/{w[0]}.md",
            )
        )

    db.import_pack(pack, pages, chunks)
    return db


@pytest.fixture(scope="module")
def large_db() -> Database:
    with tempfile.TemporaryDirectory() as tmp:
        yield _build_large_db(Path(tmp))


def _measure(db: Database, query: str, limit: int) -> dict[str, float]:
    times_ms: list[float] = []
    for _ in range(REPS):
        t0 = time.perf_counter()
        search(db, query, limit=limit)
        times_ms.append((time.perf_counter() - t0) * 1000)
    times_ms.sort()
    p95_idx = max(0, int(len(times_ms) * 0.95) - 1)
    return {
        "p50_ms": round(times_ms[len(times_ms) // 2], 3),
        "p95_ms": round(times_ms[p95_idx], 3),
        "min_ms": round(times_ms[0], 3),
        "max_ms": round(times_ms[-1], 3),
    }


def test_query_latency(large_db: Database) -> None:
    results: dict[str, object] = {
        "chunk_count": CHUNK_COUNT,
        "reps_per_query": REPS,
        "queries": {},
    }

    print(f"\n{'Query':<30} {'P50 ms':>8} {'P95 ms':>8} {'min ms':>8} {'max ms':>8}")
    print("-" * 70)

    for label, query, limit in _QUERIES:
        stats = _measure(large_db, query, limit)
        results["queries"][label] = {"query": query, "limit": limit, **stats}  # type: ignore[index]
        print(
            f"{label:<30} {stats['p50_ms']:>8.3f} {stats['p95_ms']:>8.3f}"
            f" {stats['min_ms']:>8.3f} {stats['max_ms']:>8.3f}"
        )
        assert stats["p95_ms"] < 100, (
            f"P95 latency for '{query}' is {stats['p95_ms']:.1f} ms — "
            "FTS5 performance regression (threshold: 100 ms)"
        )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "latency.json").write_text(json.dumps(results, indent=2) + "\n")
    print(f"\nResults written to {RESULTS_DIR / 'latency.json'}")
