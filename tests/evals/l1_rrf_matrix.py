"""L1-style matrix eval: {unicode61, porter} x {BM25, RRF hybrid} + vector-only.

Decides D30 step 2 (and the deferred stemmer keep/revert call) with data
instead of extrapolation: the porter re-measure showed stemming trades
direct-tier precision for paraphrase-tier recall, and reranking/fusion is
the designed mitigation for exactly that tax — so the two must be measured
together, not sequentially.

Five conditions per corpus, all scored with the same retrieval_scoring
functions as l1_retrieval.py / l2_reachability.py:

    bm25-unicode61   search_docs against the unicode61 variant DB
    bm25-porter      search_docs against the porter-migrated DB
    rrf-unicode61    RRF fusion of bm25-unicode61 + vector lists
    rrf-porter       RRF fusion of bm25-porter + vector lists
    vector-only      cosine top-K over MiniLM chunk embeddings (diagnostic)

Model-free-workflow note (the design constraint this prototypes): the BM25
lists come from the exact public search_docs path shipping today — the
vector list is strictly additive. A production hybrid built this way
degrades to today's BM25-only behavior whenever embeddings or an encoder
are absent. The embeddings here are precomputed offline (fastembed,
all-MiniLM-L6-v2, L2-normalized; chunk text = heading_path + summary +
content) and loaded from .npz caches — no model runs during scoring, which
also keeps this eval as deterministic as L1.

Usage (after generating the .npz caches and variant DBs — see
tests/evals/generation/work/):
    python tests/evals/l1_rrf_matrix.py html \\
        --output tests/evals/results/html_l1_rrf_matrix.json
    python tests/evals/l1_rrf_matrix.py pilot \\
        --output tests/evals/results/pilot_l1_rrf_matrix.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from synd.search.fts import SearchError  # noqa: E402
from synd.server import search_docs  # noqa: E402
from synd.storage.db import Database  # noqa: E402
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
_FUSION_DEPTH = 100  # per-list depth fed into RRF
_RRF_K = 60  # standard RRF constant


def rrf_fuse(lists: list[list[int]], k: int = _RRF_K) -> list[int]:
    """Reciprocal Rank Fusion: score(d) = sum over lists of 1/(k + rank(d)).

    rank is 1-based; a document absent from a list contributes nothing for
    that list. Ties broken by (first list's rank, then id) for determinism.
    """
    scores: dict[int, float] = {}
    first_rank: dict[int, int] = {}
    for lst in lists:
        for rank, doc_id in enumerate(lst, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
            if doc_id not in first_rank:
                first_rank[doc_id] = rank
    return sorted(scores, key=lambda d: (-scores[d], first_rank[d], d))


def _bm25_ranked(db: Database, query_text: str, limit: int) -> list[int]:
    """Ranked chunk ids from the public search path (same as l1_retrieval)."""
    try:
        response = search_docs(db, query=query_text, limit=limit)
    except SearchError:
        return []
    results = response.get("results")
    if not isinstance(results, list):
        return []
    return [int(r["chunk_id"]) for r in results]


class _VectorIndex:
    """Brute-force cosine top-K over precomputed L2-normalized embeddings.

    Brute force is exact and trivially fast at this corpus size (<10k
    chunks); production would use sqlite-vec ANN, which approximates this.
    """

    def __init__(self, npz_path: Path) -> None:
        data = np.load(npz_path)
        self.ids: np.ndarray = data["ids"]
        self.vecs: np.ndarray = data["vecs"]

    def top_k(self, query_vec: np.ndarray, k: int) -> list[int]:
        sims = self.vecs @ query_vec
        order = np.argsort(-sims)[:k]
        return [int(self.ids[i]) for i in order]


def _load_query_vecs(npz_path: Path) -> dict[str, np.ndarray]:
    data = np.load(npz_path)
    return {str(qid): vec for qid, vec in zip(data["ids"], data["vecs"])}


def run_matrix(corpus: str) -> dict[str, Any]:
    dataset = json.loads(_DATASETS[corpus].read_text(encoding="utf-8"))

    db_porter = Database(_WORK / f"{corpus}.db")
    db_unicode = Database(_WORK / f"{corpus}_unicode61.db")
    vindex = _VectorIndex(_WORK / f"{corpus}_chunk_embeddings.npz")
    query_vecs = _load_query_vecs(_WORK / f"{corpus}_query_embeddings.npz")

    # Fusion mixes ranked id lists from both DBs plus the vector index, so
    # all three MUST share one id space. Both DBs import the same packs in
    # the same order (fresh AUTOINCREMENT), and the embeddings were generated
    # from the porter DB's rows — verify instead of trusting it.
    map_p = dict(db_porter.conn.execute("SELECT id, content_hash FROM chunks"))
    map_u = dict(db_unicode.conn.execute("SELECT id, content_hash FROM chunks"))
    if map_p != map_u:
        raise SystemExit(
            f"{corpus}: chunk id spaces differ between porter and unicode61 "
            "DBs — rebuild the unicode61 variant with the same pack order."
        )
    hash_to_ids = load_hash_to_ids(db_porter)

    conditions = (
        "bm25-unicode61",
        "bm25-porter",
        "rrf-unicode61",
        "rrf-porter",
        "vector-only",
    )
    rows: list[dict[str, Any]] = []
    unresolved: list[str] = []

    for question in dataset.get("questions", []):
        gold_ids = resolve_gold_ids(question, hash_to_ids)
        if not gold_ids:
            unresolved.append(question["id"])
            continue

        for form in _QUERY_FORMS:
            query_text = question.get(form, "")
            if not query_text.strip():
                continue
            qvec = query_vecs[f"{question['id']}::{form}"]

            bm25_u = _bm25_ranked(db_unicode, query_text, _FUSION_DEPTH)
            bm25_p = _bm25_ranked(db_porter, query_text, _FUSION_DEPTH)
            vec = vindex.top_k(qvec, _FUSION_DEPTH)

            ranked = {
                "bm25-unicode61": bm25_u,
                "bm25-porter": bm25_p,
                "rrf-unicode61": rrf_fuse([bm25_u, vec]),
                "rrf-porter": rrf_fuse([bm25_p, vec]),
                "vector-only": vec,
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

    db_porter.close()
    db_unicode.close()

    if not rows:
        raise SystemExit(f"matrix run produced no scorable questions for {corpus}")

    def _rows(cond: str, form: str) -> list[dict[str, Any]]:
        return [r for r in rows if r["condition"] == cond and r["query_form"] == form]

    return {
        "corpus": corpus,
        "dataset": str(_DATASETS[corpus]),
        "fusion_depth": _FUSION_DEPTH,
        "rrf_k": _RRF_K,
        "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
        "n_questions": len(dataset.get("questions", [])),
        "n_gold_unresolved": len(unresolved),
        "gold_unresolved_ids": unresolved,
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
    print(f"RRF x stemming matrix — {result['corpus']} corpus")
    print(
        f"  fusion_depth={result['fusion_depth']} rrf_k={result['rrf_k']} "
        f"model={result['embedding_model']}"
    )

    for form in _QUERY_FORMS:
        print(f"\n  [{form}] overall:")
        for cond in conditions:
            m = result["overall"][cond][form]
            metrics_str = "  ".join(f"{name}={m[name]:.3f}" for name in metric_names())
            print(f"    {cond:16s} {metrics_str}")

    print("\n  [query] by difficulty tier:")
    tiers = sorted(next(iter(result["by_difficulty"].values()))["query"].keys())
    for tier in tiers:
        print(f"    {tier}:")
        for cond in conditions:
            m = result["by_difficulty"][cond]["query"][tier]
            metrics_str = "  ".join(f"{name}={m[name]:.3f}" for name in metric_names())
            print(f"      {cond:16s} n={m['n']:<4d} {metrics_str}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="L1-style matrix: tokenizer x retrieval-strategy"
    )
    parser.add_argument("corpus", choices=("html", "pilot"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    result = run_matrix(args.corpus)
    _print_summary(result)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()
