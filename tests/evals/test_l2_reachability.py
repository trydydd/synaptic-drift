from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from synd.storage.db import Database
from tests.evals.l2_reachability import _dedup_first_seen, run_l2, run_question
from tests.evals.model_client import ChatReply, ModelClientError, ToolCall

pytestmark = pytest.mark.evals


class FakeChatClient:
    """Scripted list[ChatReply]; records every chat() call's messages/tools."""

    def __init__(self, replies: list[ChatReply], model: str = "fake") -> None:
        self._replies = list(replies)
        self.model = model
        self.calls: list[dict[str, object]] = []

    def chat(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> ChatReply:
        if not self._replies:
            raise AssertionError(
                "FakeChatClient exhausted: scripted fewer replies than turns taken"
            )
        self.calls.append(
            {"messages": copy.deepcopy(messages), "tools": copy.deepcopy(tools)}
        )
        return self._replies.pop(0)


class _RaisingChatClient:
    model = "raising-fake"

    def chat(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> ChatReply:
        raise ModelClientError("connection refused")


def _text_reply(content: str) -> ChatReply:
    return ChatReply(content=content, tool_calls=[], finish_reason="stop")


def _tool_call_reply(
    name: str, arguments: dict[str, object], call_id: str = "1"
) -> ChatReply:
    return ChatReply(
        content=None,
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
        finish_reason="tool_calls",
    )


def _question(qid: str = "q1", query: str = "how do I use tools") -> dict[str, object]:
    return {"id": qid, "pack": "evalcorpus", "difficulty": "direct", "query": query}


class TestDedupFirstSeen:
    def test_concatenates_preserving_order(self) -> None:
        assert _dedup_first_seen([[1, 2], [3, 4]]) == [1, 2, 3, 4]

    def test_keeps_first_occurrence_rank(self) -> None:
        # id 2 appears at rank 1 in the second call — its first (best) rank,
        # from the first call, must win.
        assert _dedup_first_seen([[5, 2], [2, 9]]) == [5, 2, 9]

    def test_empty_input_returns_empty(self) -> None:
        assert _dedup_first_seen([]) == []


class TestRunQuestion:
    def test_no_tool_calls_returns_empty_ranked_ids(self) -> None:
        client = FakeChatClient([_text_reply("Found nothing relevant.")])
        result = run_question(client, None, _question())  # type: ignore[arg-type]

        assert result.ranked_ids == []
        assert result.fetched_ids == []
        assert result.error is None
        assert result.turns_used == 1

    def test_sends_search_and_fetch_schemas(self, eval_db: Database) -> None:
        client = FakeChatClient([_text_reply("done")])
        run_question(client, eval_db, _question())

        tools = client.calls[0]["tools"]
        assert isinstance(tools, list)
        names = [t["function"]["name"] for t in tools]  # type: ignore[index]
        assert names == ["search", "fetch"]

    def test_user_message_is_the_query_field_only(self, eval_db: Database) -> None:
        client = FakeChatClient([_text_reply("done")])
        run_question(client, eval_db, _question(query="how do tools connect"))

        messages = client.calls[0]["messages"]
        assert isinstance(messages, list)
        user_msg = next(m for m in messages if m["role"] == "user")
        assert user_msg["content"] == "how do tools connect"

    def test_search_call_records_ranked_ids(self, eval_db: Database) -> None:
        client = FakeChatClient(
            [
                _tool_call_reply("search", {"query": "tool"}),
                _text_reply("done"),
            ]
        )
        result = run_question(client, eval_db, _question())

        assert result.search_calls_made == 1
        assert result.ranked_ids  # evalcorpus contains real matches for "tool"

    def test_search_tool_message_truncated_to_requested_limit(
        self, eval_db: Database
    ) -> None:
        client = FakeChatClient(
            [
                _tool_call_reply("search", {"query": "tool", "limit": 2}),
                _text_reply("done"),
            ]
        )
        run_question(client, eval_db, _question())

        tool_msg = next(
            m
            for m in client.calls[1]["messages"]
            if m.get("role") == "tool"  # type: ignore[union-attr]
        )
        payload = json.loads(tool_msg["content"])
        assert len(payload["results"]) <= 2

    def test_scoring_sees_full_top_k_even_when_model_requests_fewer(
        self, eval_db: Database
    ) -> None:
        """The model's own view is truncated to limit=1, but ranked_ids used
        for scoring must still reflect the full top-K search_docs pull."""
        client = FakeChatClient(
            [
                _tool_call_reply("search", {"query": "tool", "limit": 1}),
                _text_reply("done"),
            ]
        )
        result = run_question(client, eval_db, _question())

        tool_msg = next(
            m
            for m in client.calls[1]["messages"]
            if m.get("role") == "tool"  # type: ignore[union-attr]
        )
        payload = json.loads(tool_msg["content"])
        assert len(payload["results"]) == 1
        # but our scoring list is not artificially capped at 1
        assert len(result.ranked_ids) > 1

    def test_fetch_call_records_fetched_ids(self, eval_db: Database) -> None:
        client = FakeChatClient(
            [
                _tool_call_reply("search", {"query": "tool"}),
                _text_reply("done"),
            ]
        )
        first = run_question(client, eval_db, _question())
        assert first.ranked_ids

        client2 = FakeChatClient(
            [
                _tool_call_reply("fetch", {"chunk_ids": [first.ranked_ids[0]]}),
                _text_reply("done"),
            ]
        )
        result = run_question(client2, eval_db, _question())
        assert result.fetched_ids == [first.ranked_ids[0]]

    def test_two_searches_are_concatenated_and_deduped(self, eval_db: Database) -> None:
        client = FakeChatClient(
            [
                _tool_call_reply("search", {"query": "tool"}, call_id="a"),
                _tool_call_reply("search", {"query": "connection"}, call_id="b"),
                _text_reply("done"),
            ]
        )
        result = run_question(client, eval_db, _question())

        assert result.search_calls_made == 2
        assert len(result.ranked_ids) == len(set(result.ranked_ids))

    def test_unknown_tool_name_does_not_crash(self, eval_db: Database) -> None:
        client = FakeChatClient(
            [
                _tool_call_reply("nonsense", {}),
                _text_reply("done"),
            ]
        )
        result = run_question(client, eval_db, _question())
        assert result.error is None
        assert result.ranked_ids == []

    def test_loop_terminates_at_max_turns(self, eval_db: Database) -> None:
        replies = [_tool_call_reply("search", {"query": "tool"}) for _ in range(10)]
        client = FakeChatClient(replies)

        result = run_question(client, eval_db, _question(), max_turns=3)

        assert result.turns_used == 3
        assert result.error == "max_turns"

    def test_model_client_error_recorded_not_raised(self, eval_db: Database) -> None:
        client = _RaisingChatClient()
        result = run_question(client, eval_db, _question())

        assert result.error is not None
        assert "connection refused" in result.error
        assert result.ranked_ids == []


class TestRunL2:
    def _dataset(self, tmp_path, gold_hash: str):  # type: ignore[no-untyped-def]
        path = tmp_path / "gold.json"
        path.write_text(
            json.dumps(
                {
                    "questions": [
                        {
                            "id": "q1",
                            "pack": "evalcorpus",
                            "difficulty": "direct",
                            "query": "how do tools connect",
                            "gold": [{"content_hash": gold_hash}],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_scores_against_gold_and_writes_result_shape(
        self, tmp_path, eval_db: Database
    ) -> None:
        row = eval_db.conn.execute(
            "SELECT id, content_hash FROM chunks LIMIT 1"
        ).fetchone()
        dataset_path = self._dataset(tmp_path, row["content_hash"])
        db_path = Path(eval_db.conn.execute("PRAGMA database_list").fetchone()[2])

        client = FakeChatClient(
            [
                _tool_call_reply("search", {"query": "tool"}),
                _text_reply("done"),
            ]
        )
        result = run_l2(dataset_path, db_path, client, max_turns=6)

        assert result["n_scored"] == 1
        assert result["n_gold_unresolved"] == 0
        assert set(result["overall"]) >= {"recall@1", "recall@5", "mrr", "ndcg@10"}
        assert result["by_difficulty"]["direct"]["n"] == 1

    def test_unresolved_gold_is_skipped_not_scored(
        self, tmp_path, eval_db: Database
    ) -> None:
        dataset_path = self._dataset(tmp_path, "sha256:does-not-exist")
        db_path = Path(eval_db.conn.execute("PRAGMA database_list").fetchone()[2])
        client = FakeChatClient([])

        with pytest.raises(SystemExit):
            run_l2(dataset_path, db_path, client, max_turns=6)
