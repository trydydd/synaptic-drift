from __future__ import annotations

import pytest

from tests.evals.eval_errors import EvalError
from tests.evals.metrics import mrr, ndcg_at_k, recall_at_k


def test_recall_at_k_partial_hit() -> None:
    assert recall_at_k([1, 2, 3, 4, 5], {2, 9}, 3) == 0.5


def test_recall_at_k_k_beyond_length() -> None:
    assert recall_at_k([1, 2, 3], {1, 2, 3}, 10) == 1.0


def test_recall_counts_duplicates_once() -> None:
    assert recall_at_k([1, 1, 2], {1, 2}, 3) == 1.0


def test_mrr_first_relevant_at_rank_three() -> None:
    assert mrr([5, 6, 7], {7}) == pytest.approx(1 / 3)


def test_mrr_no_relevant_returns_zero() -> None:
    assert mrr([5, 6, 7], {9}) == 0.0


def test_ndcg_perfect_ranking_is_one() -> None:
    assert ndcg_at_k([1, 2], {1, 2}, 2) == 1.0


def test_ndcg_relevant_at_second_position() -> None:
    assert ndcg_at_k([9, 1], {1}, 2) == pytest.approx(0.6309297535714574)


def test_empty_relevant_ids_raises_eval_error() -> None:
    with pytest.raises(EvalError):
        recall_at_k([1], set(), 5)
    with pytest.raises(EvalError):
        mrr([1], set())
    with pytest.raises(EvalError):
        ndcg_at_k([1], set(), 5)


def test_reversed_ranking_scores_strictly_lower() -> None:
    """NEG: ndcg must be sensitive to ordering, not just presence."""
    assert ndcg_at_k([9, 1], {1}, 2) < ndcg_at_k([1, 9], {1}, 2)


def test_recall_never_exceeds_one() -> None:
    """NEG: a duplicated relevant id in ranked must not push recall above 1.0."""
    assert recall_at_k([1, 1], {1}, 2) <= 1.0
