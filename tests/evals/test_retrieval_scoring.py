from __future__ import annotations

import pytest

from tests.evals.retrieval_scoring import (
    aggregate,
    metric_names,
    resolve_gold_ids,
    score_one,
    slice_by,
)

pytestmark = pytest.mark.evals


class TestResolveGoldIds:
    def test_resolves_single_gold_hash(self) -> None:
        hash_to_ids = {"sha256:abc": {7}}
        question = {"gold": [{"content_hash": "sha256:abc"}]}
        assert resolve_gold_ids(question, hash_to_ids) == {7}

    def test_unions_multiple_gold_refs(self) -> None:
        hash_to_ids = {"sha256:a": {1}, "sha256:b": {2, 3}}
        question = {
            "gold": [{"content_hash": "sha256:a"}, {"content_hash": "sha256:b"}]
        }
        assert resolve_gold_ids(question, hash_to_ids) == {1, 2, 3}

    def test_missing_hash_contributes_nothing(self) -> None:
        hash_to_ids = {"sha256:known": {1}}
        question = {"gold": [{"content_hash": "sha256:stale"}]}
        assert resolve_gold_ids(question, hash_to_ids) == set()

    def test_no_gold_field_returns_empty_set(self) -> None:
        assert resolve_gold_ids({}, {"sha256:a": {1}}) == set()


class TestScoreOne:
    def test_perfect_hit_at_rank_one(self) -> None:
        scores = score_one([5, 1, 2], {5})
        assert scores["recall@1"] == 1.0
        assert scores["mrr"] == 1.0

    def test_miss_scores_zero_everywhere(self) -> None:
        scores = score_one([1, 2, 3], {99})
        assert all(v == 0.0 for v in scores.values())

    def test_contains_all_metric_names(self) -> None:
        scores = score_one([1], {1})
        assert set(scores) == set(metric_names())


class TestAggregate:
    def test_empty_rows_returns_zeros(self) -> None:
        result = aggregate([])
        assert result == {name: 0.0 for name in metric_names()}

    def test_averages_across_rows(self) -> None:
        rows = [
            {
                "recall@1": 1.0,
                "recall@5": 1.0,
                "recall@10": 1.0,
                "recall@20": 1.0,
                "mrr": 1.0,
                "ndcg@10": 1.0,
            },
            {
                "recall@1": 0.0,
                "recall@5": 0.0,
                "recall@10": 0.0,
                "recall@20": 0.0,
                "mrr": 0.0,
                "ndcg@10": 0.0,
            },
        ]
        assert aggregate(rows)["recall@1"] == 0.5


class TestSliceBy:
    def test_groups_and_counts_by_key(self) -> None:
        rows = [
            {
                "pack": "a",
                "recall@1": 1.0,
                "recall@5": 1.0,
                "recall@10": 1.0,
                "recall@20": 1.0,
                "mrr": 1.0,
                "ndcg@10": 1.0,
            },
            {
                "pack": "a",
                "recall@1": 0.0,
                "recall@5": 0.0,
                "recall@10": 0.0,
                "recall@20": 0.0,
                "mrr": 0.0,
                "ndcg@10": 0.0,
            },
            {
                "pack": "b",
                "recall@1": 1.0,
                "recall@5": 1.0,
                "recall@10": 1.0,
                "recall@20": 1.0,
                "mrr": 1.0,
                "ndcg@10": 1.0,
            },
        ]
        result = slice_by(rows, "pack")
        assert result["a"]["n"] == 2
        assert result["a"]["recall@1"] == 0.5
        assert result["b"]["n"] == 1
