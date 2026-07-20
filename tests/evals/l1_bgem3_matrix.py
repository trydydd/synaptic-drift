"""S15 head-to-head: BGE-M3 dense+sparse vs the unicode61-BM25 + MiniLM stack.

Scores BGE-M3's dense and learned-sparse legs on the same enriched-append gold
corpora, with the same scoring functions and RRF fusion as l1_rrf_matrix.py, so
the numbers are directly comparable to the shipped-stack conditions. The BGE-M3
ranked lists come from artifacts produced by generation/gen_bgem3_artifacts.py
(run in an isolated torch venv); this scorer needs only numpy + synd.

Conditions (both corpora, both query forms, enriched-append indexed side):

    bm25                     unicode61 BM25 — the shipping path
    minilm-dense             MiniLM dense cosine (current [semantic] candidate)
    bgem3-dense              BGE-M3 dense cosine
    bgem3-sparse             BGE-M3 learned-sparse dot (the core hypothesis)
    rrf-bm25+minilm          + rrf-w3 variant (bm25 weighted 3×) — current best
    rrf-bm25+bgem3dense      + rrf-w3 variant
    rrf-bm25+bgem3sparse     + rrf-w3 variant
    rrf-bm25+bgem3both       bm25 + BGE-M3 dense + sparse; unweighted + w3(3,1,1)
    rrf-bgem3both            pure BGE-M3 dense+sparse (no BM25), diagnostic

Usage (project venv):
    python tests/evals/l1_bgem3_matrix.py html \\
        --output tests/evals/results/html_l1_bgem3_matrix.json
    python tests/evals/l1_bgem3_matrix.py pilot \\
        --output tests/evals/results/pilot_l1_bgem3_matrix.json
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

from synd.storage.db import Database  # noqa: E402
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


class _DenseIndex:
    """Brute-force cosine top-K over L2-normalized dense vectors."""

    def __init__(self, npz_path: Path) -> None:
        data = np.load(npz_path)
        self.ids: np.ndarray = data["ids"]
        self.vecs: np.ndarray = data["vecs"]

    def top_k(self, query_vec: np.ndarray, k: int) -> list[int]:
        sims = self.vecs @ query_vec
        order = np.argsort(-sims)[:k]
        return [int(self.ids[i]) for i in order]


class _SparseIndex:
    """Brute-force sparse dot-product top-K over CSR lexical weights.

    score(chunk, query) = sum over shared token ids of w_chunk[t] * w_query[t].
    Computed with numpy alone: scatter the query weights into a token->weight
    lookup, gather over the chunk nnz, and segment-sum by row via bincount
    (bincount zeroes empty rows correctly, unlike np.add.reduceat)."""

    def __init__(self, npz_path: Path) -> None:
        data = np.load(npz_path)
        self.ids: np.ndarray = data["ids"]
        self.indptr: np.ndarray = data["indptr"]
        self.indices: np.ndarray = data["indices"]
        self.data: np.ndarray = data["data"]
        n = len(self.ids)
        self.row_of = np.repeat(np.arange(n), np.diff(self.indptr))
        self._max_tok = int(self.indices.max()) if self.indices.size else 0

    def top_k(self, q_indices: np.ndarray, q_data: np.ndarray, k: int) -> list[int]:
        if q_indices.size == 0 or self.indices.size == 0:
            return []
        size = max(self._max_tok, int(q_indices.max())) + 1
        lookup = np.zeros(size, dtype=np.float32)
        lookup[q_indices] = q_data
        contrib = self.data * lookup[self.indices]
        scores = np.bincount(self.row_of, weights=contrib, minlength=len(self.ids))
        order = np.argsort(-scores)[:k]
        return [int(self.ids[i]) for i in order]


def _load_dense_queries(npz_path: Path) -> dict[str, np.ndarray]:
    data = np.load(npz_path)
    return {str(qid): vec for qid, vec in zip(data["ids"], data["vecs"])}


def _load_sparse_queries(npz_path: Path) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    data = np.load(npz_path)
    ids = data["ids"]
    indptr = data["indptr"]
    indices = data["indices"]
    vals = data["data"]
    out: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for i, qid in enumerate(ids):
        lo, hi = int(indptr[i]), int(indptr[i + 1])
        out[str(qid)] = (indices[lo:hi], vals[lo:hi])
    return out


def run_matrix(corpus: str) -> dict[str, Any]:
    dataset = json.loads(_DATASETS[corpus].read_text(encoding="utf-8"))
    base = f"{corpus}_enriched_append"

    db_bm25 = Database(_WORK / f"{base}_unicode61.db")

    minilm = _DenseIndex(_WORK / f"{base}_chunk_embeddings.npz")
    minilm_q = _load_dense_queries(_WORK / f"{corpus}_query_embeddings.npz")
    bgem3_dense = _DenseIndex(_WORK / f"{base}_bgem3_dense_chunk.npz")
    bgem3_dense_q = _load_dense_queries(_WORK / f"{corpus}_bgem3_dense_query.npz")
    bgem3_sparse = _SparseIndex(_WORK / f"{base}_bgem3_sparse_chunk.npz")
    bgem3_sparse_q = _load_sparse_queries(_WORK / f"{corpus}_bgem3_sparse_query.npz")

    # All legs must share one chunk-id space for RRF to be meaningful.
    db_ids = {int(r[0]) for r in db_bm25.conn.execute("SELECT id FROM chunks")}
    for name, ids in (
        ("minilm", minilm.ids),
        ("bgem3-dense", bgem3_dense.ids),
        ("bgem3-sparse", bgem3_sparse.ids),
    ):
        if set(int(i) for i in ids) != db_ids:
            raise SystemExit(f"{corpus}: {name} id space differs from the BM25 DB")

    hash_to_ids = load_hash_to_ids(db_bm25)

    conditions = (
        "bm25",
        "minilm-dense",
        "bgem3-dense",
        "bgem3-sparse",
        "rrf-bm25+minilm",
        "rrf-w3-bm25+minilm",
        "rrf-bm25+bgem3dense",
        "rrf-w3-bm25+bgem3dense",
        "rrf-bm25+bgem3sparse",
        "rrf-w3-bm25+bgem3sparse",
        "rrf-bm25+bgem3both",
        "rrf-w3-bm25+bgem3both",
        "rrf-bgem3both",
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
            key = f"{question['id']}::{form}"

            bm25 = _bm25_ranked(db_bm25, query_text, _FUSION_DEPTH)
            mini = minilm.top_k(minilm_q[key], _FUSION_DEPTH)
            bd = bgem3_dense.top_k(bgem3_dense_q[key], _FUSION_DEPTH)
            bs_idx, bs_data = bgem3_sparse_q[key]
            bs = bgem3_sparse.top_k(bs_idx, bs_data, _FUSION_DEPTH)

            ranked = {
                "bm25": bm25,
                "minilm-dense": mini,
                "bgem3-dense": bd,
                "bgem3-sparse": bs,
                "rrf-bm25+minilm": rrf_fuse([bm25, mini]),
                "rrf-w3-bm25+minilm": rrf_fuse([bm25, mini], weights=[3.0, 1.0]),
                "rrf-bm25+bgem3dense": rrf_fuse([bm25, bd]),
                "rrf-w3-bm25+bgem3dense": rrf_fuse([bm25, bd], weights=[3.0, 1.0]),
                "rrf-bm25+bgem3sparse": rrf_fuse([bm25, bs]),
                "rrf-w3-bm25+bgem3sparse": rrf_fuse([bm25, bs], weights=[3.0, 1.0]),
                "rrf-bm25+bgem3both": rrf_fuse([bm25, bd, bs]),
                "rrf-w3-bm25+bgem3both": rrf_fuse(
                    [bm25, bd, bs], weights=[3.0, 1.0, 1.0]
                ),
                "rrf-bgem3both": rrf_fuse([bd, bs]),
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

    latency_path = _WORK / f"{corpus}_bgem3_latency.json"
    latency = json.loads(latency_path.read_text()) if latency_path.exists() else None

    return {
        "corpus": corpus,
        "dataset": str(_DATASETS[corpus]),
        "indexed_side": base,
        "fusion_depth": _FUSION_DEPTH,
        "dense_model": "BAAI/bge-m3",
        "baseline_dense_model": "sentence-transformers/all-MiniLM-L6-v2",
        "n_questions": len(dataset.get("questions", [])),
        "n_gold_unresolved": len(unresolved),
        "gold_unresolved_ids": unresolved,
        "bgem3_encode_latency": latency,
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
    print(f"BGE-M3 vs MiniLM+BM25 — {result['corpus']} corpus")
    for form in _QUERY_FORMS:
        print(f"\n  [{form}] overall:")
        for cond in conditions:
            m = result["overall"][cond][form]
            metrics = "  ".join(f"{n}={m[n]:.3f}" for n in metric_names())
            print(f"    {cond:26s} {metrics}")
    print("\n  [query] by difficulty tier:")
    tiers = sorted(next(iter(result["by_difficulty"].values()))["query"].keys())
    for tier in tiers:
        print(f"    {tier}:")
        for cond in conditions:
            m = result["by_difficulty"][cond]["query"][tier]
            metrics = "  ".join(f"{n}={m[n]:.3f}" for n in metric_names())
            print(f"      {cond:26s} n={m['n']:<4d} {metrics}")


def main() -> None:
    parser = argparse.ArgumentParser(description="S15 BGE-M3 head-to-head")
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
