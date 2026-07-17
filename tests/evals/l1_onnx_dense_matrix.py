"""S15 follow-up: can a PyTorch-free ONNX dense model capture BGE-M3's lift?

BGE-M3's win was dense-encoder quality (see l1_bgem3_matrix.py). This ranks the
PyTorch-free fastembed dense candidates — MiniLM (current), bge-base, bge-large
— against the BGE-M3 dense reference, on the same enriched-append gold corpora
with the same BM25 path, RRF fusion, and scoring. If bge-base/large land near
BGE-M3 without torch, they are the step-2 encoder and torch stays out.

Usage (project venv):
    python tests/evals/l1_onnx_dense_matrix.py html \\
        --output tests/evals/results/html_l1_onnx_dense_matrix.json
    python tests/evals/l1_onnx_dense_matrix.py pilot \\
        --output tests/evals/results/pilot_l1_onnx_dense_matrix.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

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

# (name, chunk npz stem, query npz stem) — all relative to a corpus.
_DENSE = [
    ("minilm", "_chunk_embeddings", "_query_embeddings"),
    ("bgebase", "_bgebase_dense_chunk", "_bgebase_dense_query"),
    ("bgelarge", "_bgelarge_dense_chunk", "_bgelarge_dense_query"),
    ("bgem3", "_bgem3_dense_chunk", "_bgem3_dense_query"),
]


def run_matrix(corpus: str) -> dict[str, Any]:
    dataset = json.loads(_DATASETS[corpus].read_text(encoding="utf-8"))
    base = f"{corpus}_enriched_append"
    db_bm25 = Database(_WORK / f"{base}_unicode61.db")

    indexes: dict[str, _DenseIndex] = {}
    queries: dict[str, dict[str, Any]] = {}
    for name, chunk_stem, query_stem in _DENSE:
        # MiniLM chunk stem hangs off the enriched-append base; the rest carry
        # the base in their own filename. Query npzs are corpus-level.
        chunk_path = _WORK / (f"{base}{chunk_stem}.npz")
        query_path = _WORK / (f"{corpus}{query_stem}.npz")
        indexes[name] = _DenseIndex(chunk_path)
        queries[name] = _load_dense_queries(query_path)

    db_ids = {int(r[0]) for r in db_bm25.conn.execute("SELECT id FROM chunks")}
    for name, idx in indexes.items():
        if {int(i) for i in idx.ids} != db_ids:
            raise SystemExit(f"{corpus}: {name} id space differs from the BM25 DB")

    hash_to_ids = load_hash_to_ids(db_bm25)

    conditions = (
        "bm25",
        "minilm-dense",
        "bgebase-dense",
        "bgelarge-dense",
        "bgem3-dense",
        "rrf-w3-bm25+minilm",
        "rrf-bm25+minilm",
        "rrf-bm25+bgebase",
        "rrf-bm25+bgelarge",
        "rrf-bm25+bgem3",
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
            dense = {
                name: indexes[name].top_k(queries[name][key], _FUSION_DEPTH)
                for name in indexes
            }
            ranked = {
                "bm25": bm25,
                "minilm-dense": dense["minilm"],
                "bgebase-dense": dense["bgebase"],
                "bgelarge-dense": dense["bgelarge"],
                "bgem3-dense": dense["bgem3"],
                "rrf-w3-bm25+minilm": rrf_fuse([bm25, dense["minilm"]], weights=[3.0, 1.0]),
                "rrf-bm25+minilm": rrf_fuse([bm25, dense["minilm"]]),
                "rrf-bm25+bgebase": rrf_fuse([bm25, dense["bgebase"]]),
                "rrf-bm25+bgelarge": rrf_fuse([bm25, dense["bgelarge"]]),
                "rrf-bm25+bgem3": rrf_fuse([bm25, dense["bgem3"]]),
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

    def _latency(tag: str) -> float | None:
        p = _WORK / f"{corpus}_{tag}_latency.json"
        return json.loads(p.read_text())["per_query_ms"] if p.exists() else None

    return {
        "corpus": corpus,
        "indexed_side": base,
        "fusion_depth": _FUSION_DEPTH,
        "encode_latency_ms": {
            "bgebase": _latency("bgebase"),
            "bgelarge": _latency("bgelarge"),
        },
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
    print(f"ONNX dense candidates — {result['corpus']} corpus")
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
    parser = argparse.ArgumentParser(description="S15 ONNX dense candidates")
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
