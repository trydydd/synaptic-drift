"""L1 evaluation: model-free retrieval quality against the gold dataset.

For each gold question, runs both query forms — `query` (natural language,
as a human would type it) and `keyword_query` (well-formed search terms) —
through the public `synd.server.search_docs` API against the indexed corpus,
then scores the ranked chunk IDs against the question's gold chunk(s).

No model is in the loop: search_docs is FTS5 end to end, and the dataset was
frozen in Stage D. This measures the retrieval engine's actual ceiling, sliced
by difficulty tier, pack, and query form — see docs/eval-harness-design.md §L1.

Matching gold chunks to indexed chunk IDs is done by content_hash (the stable
join key — see stage_c_tier.py's `_load_hash_to_db_id` for why raw chunk_ids
from the generation pipeline are not valid DB lookup keys).

Usage:
    python tests/evals/l1_retrieval.py \\
        tests/evals/datasets/real/pilot_v1.json \\
        --db tests/evals/generation/work/pilot.db \\
        --output tests/evals/results/pilot_l1_baseline.json

Exit 0 on a completed run (this is a measurement, not a pass/fail gate).
Exit 1 if the dataset or DB can't be read, or every question's gold is
unresolvable (almost certainly a stale/mismatched DB).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from synd.search.fts import SearchError  # noqa: E402
from synd.server import search_docs  # noqa: E402
from synd.storage.db import Database  # noqa: E402
from tests.evals.metrics import mrr, ndcg_at_k, recall_at_k  # noqa: E402

_K_VALUES = (1, 5, 10, 20)
_NDCG_K = 10
_LIMIT = max(_K_VALUES)
_QUERY_FORMS = ("query", "keyword_query")


def _load_hash_to_ids(db: Database) -> dict[str, set[int]]:
    """Map content_hash -> set of chunk ids across the whole indexed corpus.

    A set (not a single id) because content_hash collisions are possible in
    principle (identical content chunked from two sources); scoring treats
    any of them as a valid gold hit.
    """
    out: dict[str, set[int]] = defaultdict(set)
    for row in db.conn.execute("SELECT content_hash, id FROM chunks"):
        out[row["content_hash"]].add(row["id"])
    return out


def _resolve_gold_ids(
    question: dict[str, Any], hash_to_ids: dict[str, set[int]]
) -> set[int]:
    gold_ids: set[int] = set()
    for gold in question.get("gold", []):
        gold_ids |= hash_to_ids.get(gold.get("content_hash", ""), set())
    return gold_ids


def _ranked_chunk_ids(db: Database, query_text: str) -> list[int]:
    """Run search_docs and return chunk_ids in rank order, or [] on failure.

    search_docs never raises for ordinary text, but pathological inputs (e.g.
    a hurried-persona fragment that sanitizes to only stopwords) can surface
    SearchError from the underlying FTS5 layer. Treated as zero results —
    the real system's search tool would fail exactly the same way for such a
    query, so it is legitimate retrieval-quality signal, not a runner bug.
    """
    try:
        response = search_docs(db, query=query_text, limit=_LIMIT)
    except SearchError:
        return []
    results = response.get("results")
    if not isinstance(results, list):
        return []
    return [r["chunk_id"] for r in results]


def _score_one(ranked_ids: list[int], gold_ids: set[int]) -> dict[str, float]:
    scores: dict[str, float] = {
        f"recall@{k}": recall_at_k(ranked_ids, gold_ids, k) for k in _K_VALUES
    }
    scores["mrr"] = mrr(ranked_ids, gold_ids)
    scores[f"ndcg@{_NDCG_K}"] = ndcg_at_k(ranked_ids, gold_ids, _NDCG_K)
    return scores


def _metric_names() -> list[str]:
    return [f"recall@{k}" for k in _K_VALUES] + ["mrr", f"ndcg@{_NDCG_K}"]


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, float]:
    if not rows:
        return {name: 0.0 for name in _metric_names()}
    return {
        name: round(statistics.mean(row[name] for row in rows), 4)
        for name in _metric_names()
    }


def _slice_by(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row[key]].append(row)
    return {
        value: {"n": len(group), **_aggregate(group)}
        for value, group in sorted(groups.items())
    }


def run_l1(dataset_path: Path, db_path: Path) -> dict[str, Any]:
    with dataset_path.open(encoding="utf-8") as fh:
        dataset = json.load(fh)

    db = Database(db_path)
    hash_to_ids = _load_hash_to_ids(db)

    rows: list[dict[str, Any]] = []
    unresolved: list[str] = []

    for question in dataset.get("questions", []):
        gold_ids = _resolve_gold_ids(question, hash_to_ids)
        if not gold_ids:
            unresolved.append(question["id"])
            continue

        for form in _QUERY_FORMS:
            query_text = question.get(form, "")
            ranked_ids = _ranked_chunk_ids(db, query_text) if query_text.strip() else []
            row = {
                "id": question["id"],
                "pack": question["pack"],
                "difficulty": question["difficulty"],
                "query_form": form,
                **_score_one(ranked_ids, gold_ids),
            }
            rows.append(row)

    db.close()

    if not rows:
        raise SystemExit(
            f"L1 run produced no scorable questions: {len(unresolved)} question(s) "
            "had unresolvable gold (content_hash not found in DB — stale/mismatched "
            "index?). Nothing to report."
        )

    result: dict[str, Any] = {
        "dataset": str(dataset_path),
        "dataset_description": dataset.get("description", ""),
        "corpus": dataset.get("corpus", {}),
        "db": str(db_path),
        "n_questions": len(dataset.get("questions", [])),
        "n_scored": len({r["id"] for r in rows}),
        "n_gold_unresolved": len(unresolved),
        "gold_unresolved_ids": unresolved,
        "overall": {
            form: _aggregate([r for r in rows if r["query_form"] == form])
            for form in _QUERY_FORMS
        },
        "by_difficulty": {
            form: _slice_by([r for r in rows if r["query_form"] == form], "difficulty")
            for form in _QUERY_FORMS
        },
        "by_pack": {
            form: _slice_by([r for r in rows if r["query_form"] == form], "pack")
            for form in _QUERY_FORMS
        },
        "questions": rows,
    }
    return result


def _print_summary(result: dict[str, Any]) -> None:
    print(f"L1 retrieval evaluation — {result['dataset']}")
    print(
        f"  {result['n_scored']}/{result['n_questions']} questions scored "
        f"({result['n_gold_unresolved']} gold unresolved)"
    )
    if result["gold_unresolved_ids"]:
        print(f"  unresolved: {', '.join(result['gold_unresolved_ids'])}")

    print("\n  Overall (query form vs keyword form — the query-formulation tax):")
    for form in _QUERY_FORMS:
        m = result["overall"][form]
        metrics_str = "  ".join(f"{name}={m[name]:.3f}" for name in _metric_names())
        print(f"    {form:15s} {metrics_str}")

    print("\n  By difficulty tier (query form = 'query', i.e. natural language):")
    for tier, m in result["by_difficulty"]["query"].items():
        metrics_str = "  ".join(f"{name}={m[name]:.3f}" for name in _metric_names())
        print(f"    {tier:20s} n={m['n']:<4d} {metrics_str}")

    print("\n  By pack (query form = 'query'):")
    for pack, m in result["by_pack"]["query"].items():
        metrics_str = "  ".join(f"{name}={m[name]:.3f}" for name in _metric_names())
        print(f"    {pack:20s} n={m['n']:<4d} {metrics_str}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="L1: model-free retrieval quality against the gold dataset"
    )
    parser.add_argument("dataset_path", type=Path, help="Gold dataset JSON")
    parser.add_argument("--db", type=Path, required=True, help="Indexed synd DB")
    parser.add_argument(
        "--output", type=Path, required=True, help="Baseline JSON to write"
    )
    args = parser.parse_args()

    result = run_l1(args.dataset_path, args.db)
    _print_summary(result)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()
