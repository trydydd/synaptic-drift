"""Stage C: Model-free tiering and validation.

Reads the raw query pairs from Stage B, measures actual retrieval difficulty
using Jaccard overlap and live FTS5 rank, then assigns difficulty tiers that
reflect measured retrieval distance — overriding any self-label from the generator.

Usage:
    # Build the pilot DB first:
    synd build mcp@2025-05-29 --source https://modelcontextprotocol.io/llms-full.txt \\
        --output tests/evals/generation/work/packs/
    synd add tests/evals/generation/work/packs/mcp@2025-05-29.ctx \\
        --db tests/evals/generation/work/pilot.db

    # Then tier the raw queries:
    python tests/evals/generation/stage_c_tier.py \\
        --raw-queries tests/evals/generation/work/raw_queries_mcp.jsonl \\
        --chunks     tests/evals/generation/work/chunks_mcp.jsonl \\
        --db         tests/evals/generation/work/pilot.db \\
        --pack       mcp \\
        --output     tests/evals/generation/work/tiered_mcp.jsonl \\
        [--fts-limit 50]

Tiering logic (NL query → gold chunk):
    direct:             Jaccard ≥ 0.15  OR  NL query reaches gold in FTS5 top-5
    paraphrase:         Jaccard 0.04-0.15 AND NL query reaches gold in FTS5 top-20
    vocabulary_mismatch: Jaccard < 0.04  AND NL query reaches gold in FTS5 top-50
    DROPPED:            gold chunk not found in top-50 under keyword query
                        (question is unreachable — bad generation or wrong gold)

Output JSONL: one record per question kept (all dropped questions are skipped).
Each record includes rot-guard fields: content_hash, anchor (first 80 chars).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from synd.search.fts import search_relaxed
from synd.storage.db import Database


_STOPWORDS = frozenset(
    "a an the is are was were be been being have has had do does did will would "
    "could should of in on at to for with by from as this that these those it its "
    "i we they he she you my our their his her your its".split()
)

_MIN_KW_LEN = 3

# Tier thresholds
_JACCARD_DIRECT = 0.15
_JACCARD_PARAPHRASE_MIN = 0.04
_FTS5_DIRECT_RANK = 5
_FTS5_PARAPHRASE_RANK = 20
_FTS5_VOCABMISS_RANK = 50
_FTS5_ORACLE_RANK = (
    50  # keyword query must reach gold within this rank to keep question
)


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"\b[a-zA-Z][a-zA-Z0-9]{2,}\b", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def _jaccard(query: str, content: str) -> float:
    q_terms = _tokenize(query)
    c_terms = _tokenize(content)
    if not q_terms or not c_terms:
        return 0.0
    return len(q_terms & c_terms) / len(q_terms | c_terms)


def _fts5_rank(
    db: Database,
    query: str,
    pack_name: str,
    gold_chunk_id: int,
    limit: int,
) -> int | None:
    """Return 1-based rank of gold_chunk_id in FTS5 results, or None if not found."""
    try:
        results, _ = search_relaxed(
            db,
            query,
            packages=[pack_name],
            detail="summary",
            limit=limit,
        )
    except Exception:
        return None
    for rank, r in enumerate(results, 1):
        if r.chunk_id == gold_chunk_id:
            return rank
    return None


def _assign_tier(
    jaccard: float,
    nl_rank: int | None,
) -> str | None:
    """Return difficulty tier string, or None to drop the question."""
    if nl_rank is not None and nl_rank <= _FTS5_DIRECT_RANK:
        return "direct"
    if jaccard >= _JACCARD_DIRECT:
        return "direct"
    if nl_rank is not None and nl_rank <= _FTS5_PARAPHRASE_RANK:
        return "paraphrase"
    if jaccard >= _JACCARD_PARAPHRASE_MIN:
        return "paraphrase"
    if nl_rank is not None and nl_rank <= _FTS5_VOCABMISS_RANK:
        return "vocabulary_mismatch"
    return None  # drop: not reachable even in NL top-50


def _anchor(content: str, length: int = 80) -> str:
    """First {length} printable characters of content (collapse whitespace)."""
    cleaned = " ".join(content.split())
    return cleaned[:length]


def tier_queries(
    raw_queries_path: Path,
    chunks_path: Path,
    db_path: Path,
    pack_name: str,
    output_path: Path,
    fts_limit: int,
) -> None:
    # Load chunk content index for Jaccard
    chunk_content: dict[int, str] = {}
    chunk_hash: dict[int, str] = {}
    with chunks_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec["pack_name"] == pack_name:
                chunk_content[rec["chunk_id"]] = rec["content"]
                chunk_hash[rec["chunk_id"]] = rec["content_hash"]

    db = Database(db_path)

    kept = dropped = 0
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with (
        raw_queries_path.open(encoding="utf-8") as inp,
        output_path.open("w", encoding="utf-8") as out,
    ):
        for line in inp:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec["pack_name"] != pack_name:
                continue

            chunk_id: int = rec["chunk_id"]
            nl_query: str = rec["nl_query"]
            kw_query: str = rec["keyword_query"]
            content = chunk_content.get(chunk_id, "")
            content_hash = chunk_hash.get(chunk_id, rec.get("content_hash", ""))

            if not nl_query.strip() or not kw_query.strip():
                dropped += 1
                continue

            # Oracle check: keyword query must reach gold in top-N
            kw_rank = _fts5_rank(db, kw_query, pack_name, chunk_id, fts_limit)
            if kw_rank is None:
                print(
                    f"  DROP chunk {chunk_id} [{rec['persona']}]: "
                    f"keyword '{kw_query[:40]}' can't reach gold in top-{fts_limit}"
                )
                dropped += 1
                continue

            nl_rank = _fts5_rank(db, nl_query, pack_name, chunk_id, fts_limit)
            j = _jaccard(nl_query, content)
            tier = _assign_tier(j, nl_rank)

            if tier is None:
                print(
                    f"  DROP chunk {chunk_id} [{rec['persona']}]: "
                    f"NL '{nl_query[:40]}' unreachable (jaccard={j:.3f}, nl_rank=None)"
                )
                dropped += 1
                continue

            out_rec = {
                "pack_name": pack_name,
                "chunk_id": chunk_id,
                "heading_path": rec["heading_path"],
                "source_url": rec["source_url"],
                "content_hash": content_hash,
                "anchor": _anchor(content),
                "capability": rec["capability"],
                "model": rec["model"],
                "persona": rec["persona"],
                "difficulty": tier,
                "nl_query": nl_query,
                "keyword_query": kw_query,
                "jaccard": round(j, 4),
                "kw_rank": kw_rank,
                "nl_rank": nl_rank,
            }
            out.write(json.dumps(out_rec, ensure_ascii=False) + "\n")
            kept += 1
            print(
                f"  KEEP chunk {chunk_id} [{rec['persona']} → {tier}] "
                f"j={j:.3f} nl_rank={nl_rank}"
            )

    db.close()
    total = kept + dropped
    print(f"\nTiering complete: {kept}/{total} kept, {dropped} dropped → {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage C: measured tiering")
    parser.add_argument("--raw-queries", type=Path, required=True)
    parser.add_argument(
        "--chunks", type=Path, required=True, help="extract_chunks output"
    )
    parser.add_argument("--db", type=Path, required=True, help="Pilot synd DB path")
    parser.add_argument(
        "--pack", required=True, help="Pack name to filter (e.g. 'mcp')"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fts-limit", type=int, default=_FTS5_ORACLE_RANK)
    args = parser.parse_args()

    tier_queries(
        args.raw_queries,
        args.chunks,
        args.db,
        args.pack,
        args.output,
        args.fts_limit,
    )


if __name__ == "__main__":
    main()
