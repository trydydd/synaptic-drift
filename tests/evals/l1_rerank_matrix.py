"""S14 spike, step 1: does a local ONNX cross-encoder reranker lift the fused
top-k, and what does it cost per query?

Context7's server-side reranker beats the shipped BM25+dense hybrid on
page-recall@1 despite parity at recall@5 (docs/hybrid-search.md); the L2
confirmation run found the same gap from the agent side — retrieval surfaces
gold far more often post-enrichment, but the agent's gold-fetch rate barely
moves, meaning the residual failure is selection among already-surfaced
results, exactly what a reranker targets.

This measures whether a small (Xenova/ms-marco-MiniLM-L-6-v2, ~80MB, no
torch) cross-encoder reranking the top _RERANK_DEPTH of the best fusion
condition (rrf-bm25+bgelarge, per l1_onnx_dense_matrix.py) closes any of that
gap, and what the wall-clock cost is (S14: "measure, don't estimate — a
search that takes 2s is a different product").

Out of scope here (S14 item 2, needs a separate L2 run): the agent-as-
reranker comparison — whether reranked order actually changes what a calling
model fetches versus reading all top-5 summaries anyway.

Usage (project venv — fastembed only, no torch):
    python tests/evals/l1_rerank_matrix.py html \\
        --output tests/evals/results/html_l1_rerank_matrix.json
    python tests/evals/l1_rerank_matrix.py pilot \\
        --output tests/evals/results/pilot_l1_rerank_matrix.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from fastembed.rerank.cross_encoder import TextCrossEncoder  # noqa: E402

from synd.storage.db import Database  # noqa: E402
from tests.evals.l1_bgem3_matrix import _DenseIndex, _load_dense_queries  # noqa: E402
from tests.evals.l1_rrf_matrix import (  # noqa: E402
    _FUSION_DEPTH,
    _bm25_ranked,
    rrf_fuse,
)
from tests.evals.retrieval_scoring import (  # noqa: E402
    aggregate,
    load_hash_to_ids,
    metric_names,
    resolve_gold_ids,
    score_one,
    slice_by,
)

_WORK = Path(__file__).parent / "generation" / "work"
_DATASETS = {
    "html": Path(__file__).parent / "datasets" / "real" / "html_v1.json",
    "pilot": Path(__file__).parent / "datasets" / "real" / "pilot_v1.json",
}
_QUERY_FORMS = ("query", "keyword_query")
_RERANK_DEPTH = 20  # candidates handed to the cross-encoder; matches max(K_VALUES)
_RERANK_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"


def _chunk_texts(base: str) -> dict[int, str]:
    conn = sqlite3.connect(_WORK / f"{base}.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, heading_path, summary, content FROM chunks"
    ).fetchall()
    conn.close()
    return {
        int(r["id"]): "\n".join(
            filter(None, (r["heading_path"], r["summary"], r["content"]))
        )
        for r in rows
    }


def run_matrix(corpus: str, rerank_model: str = _RERANK_MODEL) -> dict[str, Any]:
    dataset = json.loads(_DATASETS[corpus].read_text(encoding="utf-8"))
    base = f"{corpus}_enriched_append"
    db_bm25 = Database(_WORK / f"{base}_unicode61.db")

    bgelarge = _DenseIndex(_WORK / f"{base}_bgelarge_dense_chunk.npz")
    bgelarge_q = _load_dense_queries(_WORK / f"{corpus}_bgelarge_dense_query.npz")
    texts = _chunk_texts(base)

    hash_to_ids = load_hash_to_ids(db_bm25)
    reranker = TextCrossEncoder(rerank_model)
    # Warmup: first call pays ONNX session + graph-optimization cost.
    list(reranker.rerank("warmup query", ["warmup document"] * 4))

    conditions = ("bm25", "rrf-bm25+bgelarge", "rerank-bm25+bgelarge")
    rows: list[dict[str, Any]] = []
    unresolved: list[str] = []
    rerank_latency_ms: list[float] = []

    for question in dataset.get("questions", []):
        gold_ids = resolve_gold_ids(question, hash_to_ids)
        if not gold_ids:
            unresolved.append(question["id"])
            continue
        for form in _QUERY_FORMS:
            query_text = question.get(form, "")
            if not query_text.strip():
                continue

            bm25 = _bm25_ranked(db_bm25, query_text, _FUSION_DEPTH)
            dense = bgelarge.top_k(
                bgelarge_q[f"{question['id']}::{form}"], _FUSION_DEPTH
            )
            fused = rrf_fuse([bm25, dense])

            candidates = fused[:_RERANK_DEPTH]
            candidate_texts = [texts[cid] for cid in candidates]
            t0 = time.perf_counter()
            scores = list(reranker.rerank(query_text, candidate_texts))
            rerank_latency_ms.append(1000.0 * (time.perf_counter() - t0))
            reranked = [
                cid
                for cid, _ in sorted(zip(candidates, scores), key=lambda pair: -pair[1])
            ]

            ranked = {
                "bm25": bm25,
                "rrf-bm25+bgelarge": fused,
                "rerank-bm25+bgelarge": reranked,
            }
            for cond in conditions:
                rows.append(
                    {
                        "id": question["id"],
                        "pack": question["pack"],
                        "difficulty": question["difficulty"],
                        "query_form": form,
                        "condition": cond,
                        **score_one(ranked[cond], gold_ids),
                    }
                )

    db_bm25.close()
    if not rows:
        raise SystemExit(f"no scorable questions for {corpus}")

    def _rows(cond: str, form: str) -> list[dict[str, Any]]:
        return [r for r in rows if r["condition"] == cond and r["query_form"] == form]

    n = len(rerank_latency_ms)
    sorted_lat = sorted(rerank_latency_ms)
    latency = {
        "n_queries": n,
        "mean_ms": round(sum(sorted_lat) / n, 2) if n else None,
        "p50_ms": round(sorted_lat[n // 2], 2) if n else None,
        "p90_ms": round(sorted_lat[int(n * 0.9)], 2) if n else None,
        "max_ms": round(sorted_lat[-1], 2) if n else None,
    }

    return {
        "corpus": corpus,
        "indexed_side": base,
        "rerank_model": rerank_model,
        "rerank_depth": _RERANK_DEPTH,
        "fusion_depth": _FUSION_DEPTH,
        "rerank_latency": latency,
        "n_questions": len(dataset.get("questions", [])),
        "n_gold_unresolved": len(unresolved),
        "overall": {
            cond: {form: aggregate(_rows(cond, form)) for form in _QUERY_FORMS}
            for cond in conditions
        },
        "by_difficulty": {
            cond: {
                form: slice_by(_rows(cond, form), "difficulty") for form in _QUERY_FORMS
            }
            for cond in conditions
        },
        "questions": rows,
    }


def _print_summary(result: dict[str, Any]) -> None:
    conditions = list(result["overall"].keys())
    print(f"Reranker spike (S14) — {result['corpus']} corpus")
    lat = result["rerank_latency"]
    print(
        f"  rerank latency (top-{result['rerank_depth']}, "
        f"n={lat['n_queries']}): mean={lat['mean_ms']}ms "
        f"p50={lat['p50_ms']}ms p90={lat['p90_ms']}ms max={lat['max_ms']}ms"
    )
    for form in _QUERY_FORMS:
        print(f"\n  [{form}] overall:")
        for cond in conditions:
            m = result["overall"][cond][form]
            metrics = "  ".join(f"{n}={m[n]:.3f}" for n in metric_names())
            print(f"    {cond:22s} {metrics}")
    print("\n  [query] by difficulty tier:")
    tiers = sorted(next(iter(result["by_difficulty"].values()))["query"].keys())
    for tier in tiers:
        print(f"    {tier}:")
        for cond in conditions:
            m = result["by_difficulty"][cond]["query"][tier]
            metrics = "  ".join(f"{n}={m[n]:.3f}" for n in metric_names())
            print(f"      {cond:22s} n={m['n']:<4d} {metrics}")


def main() -> None:
    parser = argparse.ArgumentParser(description="S14 local reranker spike")
    parser.add_argument("corpus", choices=("html", "pilot"))
    parser.add_argument(
        "--model", default=_RERANK_MODEL, help="fastembed reranker model id"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    result = run_matrix(args.corpus, rerank_model=args.model)
    _print_summary(result)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()
