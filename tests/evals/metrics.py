"""Retrieval quality metrics: recall@k, MRR, nDCG@k.

Model-free scoring functions for a ranked list of retrieved IDs against a set
of relevant (gold) IDs. Relevance is binary — an ID either is or isn't gold.
Shared by the L1 runner (`tests/evals/l1_retrieval.py`) and, later, L2.
"""

from __future__ import annotations

import math


def recall_at_k(ranked_ids: list[int], gold_ids: set[int], k: int) -> float:
    """Fraction of gold_ids present in the top-k of ranked_ids.

    Returns 0.0 when gold_ids is empty (nothing to find, so nothing recalled).
    """
    if not gold_ids:
        return 0.0
    top_k = set(ranked_ids[:k])
    return len(top_k & gold_ids) / len(gold_ids)


def reciprocal_rank(ranked_ids: list[int], gold_ids: set[int]) -> float:
    """1/rank of the first gold hit in ranked_ids, or 0.0 if none found."""
    for rank, item in enumerate(ranked_ids, start=1):
        if item in gold_ids:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(ranked_ids: list[int], gold_ids: set[int], k: int) -> float:
    """Normalized discounted cumulative gain at k, binary relevance."""
    if not gold_ids:
        return 0.0
    dcg = 0.0
    for i, item in enumerate(ranked_ids[:k], start=1):
        if item in gold_ids:
            dcg += 1.0 / math.log2(i + 1)
    ideal_hits = min(len(gold_ids), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    if idcg == 0.0:
        return 0.0
    return dcg / idcg
