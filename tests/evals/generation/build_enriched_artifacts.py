"""Build the enriched-corpus artifacts for the D30 summary-enrichment measure.

Takes the LLM summaries produced by enrich_summaries.py and materializes
everything the matrix eval needs, as *_enriched variants alongside the
originals (which stay untouched, so baseline conditions remain rerunnable):

  1. Copy <corpus>.db and <corpus>_unicode61.db to *_enriched twins.
  2. UPDATE chunks.summary from the summaries JSONL (joined on content_hash).
  3. Rebuild chunks_fts in each twin — the schema has INSERT/DELETE triggers
     only, so an UPDATE leaves the external-content FTS index stale until the
     FTS5 'rebuild' command re-reads the content table.
  4. Re-embed all chunks (heading_path + summary + content, same recipe and
     model as the baseline embeddings) to <corpus>_enriched_chunk_embeddings.npz.

Query embeddings are unchanged — only the indexed side moves.

Usage:
    python tests/evals/generation/build_enriched_artifacts.py \\
        --corpus html \\
        --summaries tests/evals/generation/work/html_llm_summaries.jsonl
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from pathlib import Path

import numpy as np
from fastembed import TextEmbedding

_WORK = Path(__file__).parent / "work"
_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def _apply_summaries(db_path: Path, by_hash: dict[str, str]) -> tuple[int, int]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    updated = 0
    missing = 0
    for row in conn.execute("SELECT id, content_hash FROM chunks").fetchall():
        summary = by_hash.get(row["content_hash"])
        if summary is None:
            missing += 1
            continue
        conn.execute("UPDATE chunks SET summary = ? WHERE id = ?", (summary, row["id"]))
        updated += 1
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()
    return updated, missing


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", choices=("html", "pilot"), required=True)
    parser.add_argument("--summaries", type=Path, required=True)
    args = parser.parse_args()

    by_hash: dict[str, str] = {}
    with args.summaries.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rec = json.loads(line)
                by_hash[rec["content_hash"]] = rec["summary"]
    print(f"loaded {len(by_hash)} summaries")

    for variant in ("", "_unicode61"):
        src = _WORK / f"{args.corpus}{variant}.db"
        dst = _WORK / f"{args.corpus}_enriched{variant}.db"
        shutil.copy(src, dst)
        updated, missing = _apply_summaries(dst, by_hash)
        print(f"{dst.name}: {updated} summaries applied, {missing} chunks without")

    # Re-embed from the enriched porter twin (id space identical to baseline).
    conn = sqlite3.connect(_WORK / f"{args.corpus}_enriched.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, heading_path, summary, content FROM chunks ORDER BY id"
    ).fetchall()
    conn.close()
    texts = [
        "\n".join(filter(None, (r["heading_path"], r["summary"], r["content"])))
        for r in rows
    ]
    ids = np.array([r["id"] for r in rows], dtype=np.int64)
    model = TextEmbedding(_EMBEDDING_MODEL)
    vecs = np.array(list(model.embed(texts)), dtype=np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    out = _WORK / f"{args.corpus}_enriched_chunk_embeddings.npz"
    np.savez(out, ids=ids, vecs=vecs)
    print(f"embedded {len(ids)} chunks -> {out.name}")


if __name__ == "__main__":
    main()
