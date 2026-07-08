"""Retrieval quality metrics: recall@k, MRR, nDCG@k.

Pure ranking-quality functions — no I/O, no database, no randomness. Each
takes a ranked list of chunk ids (search output order, best first) and a set
of relevant ids (the gold answer) and returns a float in [0.0, 1.0].

Relevance is binary. An empty relevant_ids set is a dataset bug (a question
with no gold answer), not a valid input to score against — all three
functions raise EvalError rather than silently returning 0.0.
"""

from __future__ import annotations

import math

from tests.evals.eval_errors import EvalError


def _check_inputs(relevant_ids: set[int], k: int | None) -> None:
    if not relevant_ids:
        raise EvalError("relevant_ids is empty — a question must have a gold answer")
    if k is not None and k < 1:
        raise EvalError(f"k must be >= 1, got {k}")


def recall_at_k(ranked_ids: list[int], relevant_ids: set[int], k: int) -> float:
    """Fraction of distinct relevant_ids present in the top-k of ranked_ids."""
    _check_inputs(relevant_ids, k)
    top_k = set(ranked_ids[:k])
    return len(top_k & relevant_ids) / len(relevant_ids)


def mrr(ranked_ids: list[int], relevant_ids: set[int]) -> float:
    """1/rank of the first relevant id in ranked_ids, or 0.0 if none found."""
    _check_inputs(relevant_ids, None)
    for rank, item in enumerate(ranked_ids, start=1):
        if item in relevant_ids:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(ranked_ids: list[int], relevant_ids: set[int], k: int) -> float:
    """Normalized discounted cumulative gain at k, binary relevance."""
    _check_inputs(relevant_ids, k)
    dcg = 0.0
    for i, item in enumerate(ranked_ids[:k], start=1):
        if item in relevant_ids:
            dcg += 1.0 / math.log2(i + 1)
    ideal_hits = min(len(relevant_ids), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    return dcg / idcg
