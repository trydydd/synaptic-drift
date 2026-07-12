from __future__ import annotations

import pytest

# l1_rrf_matrix imports numpy, which is not a project dependency — it is
# installed alongside fastembed only where the matrix prototype runs.
np = pytest.importorskip("numpy")

from tests.evals.l1_rrf_matrix import rrf_fuse  # noqa: E402

pytestmark = pytest.mark.evals


class TestRrfFuse:
    def test_doc_in_both_lists_beats_doc_in_one(self) -> None:
        # doc 1 is rank 1 in both lists; doc 2 is rank 2 in one list only
        fused = rrf_fuse([[1, 2], [1]])
        assert fused[0] == 1

    def test_consensus_beats_single_top_rank(self) -> None:
        # doc 9 tops list A but is absent from B; doc 5 is rank 2 in both.
        # 2/(k+2) > 1/(k+1) for k=60 — consensus wins.
        fused = rrf_fuse([[9, 5], [7, 5]])
        assert fused[0] == 5

    def test_single_list_preserves_order(self) -> None:
        assert rrf_fuse([[3, 1, 2]]) == [3, 1, 2]

    def test_empty_lists_return_empty(self) -> None:
        assert rrf_fuse([[], []]) == []

    def test_absent_doc_contributes_nothing(self) -> None:
        # doc 8 appears only deep in one list; docs in both lists outrank it
        fused = rrf_fuse([[1, 2, 8], [2, 1]])
        assert fused.index(8) == 2

    def test_deterministic_tie_break(self) -> None:
        # identical scores: same rank in disjoint lists — first-rank equal,
        # falls back to id ascending
        fused = rrf_fuse([[4], [2]])
        assert fused == [2, 4]

    def test_k_dampens_rank_differences(self) -> None:
        # sanity on the k parameter: with huge k, rank differences vanish
        # and membership count dominates
        fused = rrf_fuse([[1, 2, 3], [3]], k=10_000)
        assert fused[0] == 3
