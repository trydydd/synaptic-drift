"""Shared gold-resolution and aggregation logic for L1 and L2 retrieval evals.

Both eval layers score a ranked chunk_id list against a question's gold
chunk(s) and aggregate per-question rows the same way; only how the ranked
list is produced differs (L1: direct search_docs call: L2: a live model's
self-authored search/fetch loop). Keeping this logic in one place means the
two layers are actually comparable — reachability_gap = L1_recall -
L2_recall only means something if both sides compute recall identically.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from tests.evals.metrics import mrr, ndcg_at_k, recall_at_k

if TYPE_CHECKING:
    from synd.storage.db import Database

K_VALUES = (1, 5, 10, 20)
NDCG_K = 10


def load_hash_to_ids(db: "Database") -> dict[str, set[int]]:
    """Map content_hash -> set of chunk ids across the whole indexed corpus.

    A set (not a single id) because content_hash collisions are possible in
    principle (identical content chunked from two sources); scoring treats
    any of them as a valid gold hit.
    """
    out: dict[str, set[int]] = defaultdict(set)
    for row in db.conn.execute("SELECT content_hash, id FROM chunks"):
        out[row["content_hash"]].add(row["id"])
    return out


def resolve_gold_ids(
    question: dict[str, Any], hash_to_ids: dict[str, set[int]]
) -> set[int]:
    """Join a gold question's content_hash refs to indexed chunk ids.

    content_hash is the stable join key — raw chunk_ids from the generation
    pipeline are not valid DB lookup keys (see stage_c_tier.py's
    _load_hash_to_db_id for why).
    """
    gold_ids: set[int] = set()
    for gold in question.get("gold", []):
        gold_ids |= hash_to_ids.get(gold.get("content_hash", ""), set())
    return gold_ids


def metric_names() -> list[str]:
    return [f"recall@{k}" for k in K_VALUES] + ["mrr", f"ndcg@{NDCG_K}"]


def score_one(ranked_ids: list[int], gold_ids: set[int]) -> dict[str, float]:
    scores: dict[str, float] = {
        f"recall@{k}": recall_at_k(ranked_ids, gold_ids, k) for k in K_VALUES
    }
    scores["mrr"] = mrr(ranked_ids, gold_ids)
    scores[f"ndcg@{NDCG_K}"] = ndcg_at_k(ranked_ids, gold_ids, NDCG_K)
    return scores


def aggregate(rows: list[dict[str, Any]]) -> dict[str, float]:
    if not rows:
        return {name: 0.0 for name in metric_names()}
    return {
        name: round(statistics.mean(row[name] for row in rows), 4)
        for name in metric_names()
    }


def slice_by(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row[key]].append(row)
    return {
        value: {"n": len(group), **aggregate(group)}
        for value, group in sorted(groups.items())
    }
