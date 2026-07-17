"""L2 evaluation: agent retrieval competence — a real model authoring its own
search queries, instead of L1's fixed gold query string.

For each gold question, a live served model is given ONLY the natural-
language `query` field (as a real user would type it) plus the 'search' and
'fetch' tools (same schemas as the real MCP server / tests/evals/endtask.py),
and is left to formulate, reformulate, and retry its own queries — including
any acronym expansion or synonym substitution it chooses to do, which is
exactly the compensating behavior L1 cannot measure since L1 sends the gold
query string directly to search_docs with no model in the loop (see
docs/eval-harness-design.md §L2, and docs/decisions.md D25/D29's html_v1
addendum for why this comparison matters).

Methodology, chosen to keep L1 and L2 comparable so
reachability_gap = L1_recall - L2_recall means something:

- search/fetch are dispatched to the SAME public API L1 uses (search_docs /
  fetch_docs), unscoped by package — matching L1's _ranked_chunk_ids, which
  also never restricts to the question's own pack.
- Every search dispatch internally requests the same top-K (K_VALUES max,
  i.e. 20) that L1 always pulls, regardless of the `limit` argument the
  model supplies — otherwise recall@10/@20 would be structurally unscoreable
  whenever a model asks for fewer results. The JSON returned TO THE MODEL is
  still truncated to what it actually asked for (or the schema default of 5)
  — the model's own choice of limit is real behavior worth preserving, it
  just doesn't starve our own scoring of the ranks beyond that cutoff.
- A question may trigger several search calls (the model reformulating after
  a poor first attempt). All ranked lists across the whole run are
  concatenated in call order and de-duplicated keeping each chunk's FIRST
  (best) occurrence — this is the single ranked_ids list scored with the
  exact same recall_at_k/mrr/ndcg_at_k functions L1 uses.
- Only the `query` (natural-language) form is evaluated — `keyword_query` is
  itself a human-authored reformulation, so running it through a model here
  would blur "what did the model do with a raw question" (the L2 question)
  with L1's own query-formulation-tax measurement.

Usage:
    python tests/evals/l2_reachability.py \\
        tests/evals/datasets/real/html_v1.json \\
        --db tests/evals/generation/work/html.db \\
        --output tests/evals/results/html_l2_reachability.json \\
        [--l1-baseline tests/evals/results/html_l1_baseline.json] \\
        [--max-turns 6] [--limit N]

Requires SYND_EVAL_BASE_URL / SYND_EVAL_MODEL (see tests/evals/model_client.py
client_from_env()). Never run in CI — this drives a real model endpoint.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from synd.search.fts import SearchError  # noqa: E402
from synd.server import fetch_docs, search_docs  # noqa: E402
from synd.storage.db import Database  # noqa: E402
from tests.evals.endtask import FETCH_TOOL_SCHEMA, SEARCH_TOOL_SCHEMA  # noqa: E402
from tests.evals.model_client import ChatReply, ModelClientError, ToolCall  # noqa: E402
from tests.evals.retrieval_scoring import (  # noqa: E402
    K_VALUES,
    aggregate,
    load_hash_to_ids,
    metric_names,
    resolve_gold_ids,
    score_one,
    slice_by,
)

_LIMIT = max(K_VALUES)
_DEFAULT_SEARCH_LIMIT = 5  # matches SEARCH_TOOL_SCHEMA's declared default

SYSTEM_PROMPT = (
    "You are a documentation retrieval assistant. Use the 'search' and "
    "'fetch' tools to find documentation that answers the user's question. "
    "Search matches your terms independently and ranks results by "
    "relevance — a few distinctive terms or a natural-language question "
    "both work. If your first search doesn't surface anything useful, try "
    "different terms (synonyms, expanded acronyms, related vocabulary) "
    "rather than giving up. Once you've found and fetched the chunks that "
    "answer the question, reply with a one-sentence confirmation — do not "
    "write code or a full answer."
)


class ChatClientProtocol(Protocol):
    def chat(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> ChatReply: ...


@dataclass
class QuestionRunResult:
    id: str
    pack: str
    difficulty: str
    ranked_ids: list[int]  # de-duplicated, first-occurrence order across all searches
    fetched_ids: list[int]
    search_calls_made: int
    tool_calls_made: int
    turns_used: int
    error: str | None


def _dispatch_search(db: Database, call: ToolCall) -> tuple[str, list[int]]:
    """Run the model's search call for real; return (tool_message_json, ranked_ids).

    ranked_ids is always the full top-_LIMIT list for scoring. The JSON
    returned to the model is truncated to what it actually asked for.
    """
    try:
        response = search_docs(
            db, query=str(call.arguments.get("query", "")), limit=_LIMIT
        )
    except SearchError as exc:
        return json.dumps({"error": str(exc), "results": []}), []

    results = response.get("results")
    if not isinstance(results, list):
        return json.dumps(response), []
    ranked_ids = [int(r["chunk_id"]) for r in results]

    requested_limit = call.arguments.get("limit", _DEFAULT_SEARCH_LIMIT)
    try:
        requested_limit = int(requested_limit)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        requested_limit = _DEFAULT_SEARCH_LIMIT
    truncated = dict(response)
    truncated["results"] = results[:requested_limit]
    return json.dumps(truncated), ranked_ids


def _dispatch_fetch(db: Database, call: ToolCall) -> tuple[str, list[int]]:
    raw_ids = call.arguments.get("chunk_ids", [])
    if not isinstance(raw_ids, list):
        return json.dumps({"error": "chunk_ids must be a list", "results": []}), []
    chunk_ids = [int(i) for i in raw_ids]
    response = fetch_docs(db, chunk_ids=chunk_ids)
    results = response.get("results")
    fetched_ids = (
        [int(r["chunk_id"]) for r in results] if isinstance(results, list) else []
    )
    return json.dumps(response), fetched_ids


def _tool_call_message(reply: ChatReply) -> dict[str, object]:
    return {
        "role": "assistant",
        "content": reply.content,
        "tool_calls": [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
            }
            for c in reply.tool_calls
        ],
    }


def _dedup_first_seen(ranked_id_lists: list[list[int]]) -> list[int]:
    """Concatenate ranked lists in call order, keeping each id's first (best) rank."""
    seen: set[int] = set()
    out: list[int] = []
    for ranked in ranked_id_lists:
        for chunk_id in ranked:
            if chunk_id not in seen:
                seen.add(chunk_id)
                out.append(chunk_id)
    return out


def run_question(
    client: ChatClientProtocol,
    db: Database,
    question: dict[str, Any],
    max_turns: int = 6,
) -> QuestionRunResult:
    """Run one gold question through a live search/fetch agent loop.

    Terminates when the model stops calling tools or max_turns is reached
    (error='max_turns' — always terminates, even against a model that never
    stops calling tools).
    """
    messages: list[dict[str, object]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question.get("query", "")},
    ]
    tools = [SEARCH_TOOL_SCHEMA, FETCH_TOOL_SCHEMA]

    search_result_lists: list[list[int]] = []
    fetched_ids: list[int] = []
    tool_calls_made = 0
    search_calls_made = 0
    turns_used = 0

    def _result(error: str | None) -> QuestionRunResult:
        return QuestionRunResult(
            id=question["id"],
            pack=question["pack"],
            difficulty=question["difficulty"],
            ranked_ids=_dedup_first_seen(search_result_lists),
            fetched_ids=fetched_ids,
            search_calls_made=search_calls_made,
            tool_calls_made=tool_calls_made,
            turns_used=turns_used,
            error=error,
        )

    try:
        for turn in range(1, max_turns + 1):
            turns_used = turn
            reply = client.chat(messages, tools=tools)

            if not reply.tool_calls:
                return _result(None)

            messages.append(_tool_call_message(reply))
            for call in reply.tool_calls:
                tool_calls_made += 1
                if call.name == "search":
                    search_calls_made += 1
                    content, ranked = _dispatch_search(db, call)
                    search_result_lists.append(ranked)
                elif call.name == "fetch":
                    content, fetched = _dispatch_fetch(db, call)
                    fetched_ids.extend(fetched)
                else:
                    content = json.dumps({"error": f"unknown tool: {call.name}"})
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": content}
                )
    except ModelClientError as exc:
        return _result(str(exc))

    return _result("max_turns")


def run_l2(
    dataset_path: Path,
    db_path: Path,
    client: ChatClientProtocol,
    max_turns: int = 6,
    question_limit: int | None = None,
) -> dict[str, Any]:
    with dataset_path.open(encoding="utf-8") as fh:
        dataset = json.load(fh)

    db = Database(db_path)
    hash_to_ids = load_hash_to_ids(db)

    questions = dataset.get("questions", [])
    if question_limit is not None:
        questions = questions[:question_limit]

    rows: list[dict[str, Any]] = []
    unresolved: list[str] = []
    runs: list[dict[str, Any]] = []

    for question in questions:
        gold_ids = resolve_gold_ids(question, hash_to_ids)
        if not gold_ids:
            unresolved.append(question["id"])
            continue

        run = run_question(client, db, question, max_turns=max_turns)
        row = {
            "id": question["id"],
            "pack": question["pack"],
            "difficulty": question["difficulty"],
            **score_one(run.ranked_ids, gold_ids),
        }
        rows.append(row)
        runs.append(
            {
                "id": run.id,
                "search_calls_made": run.search_calls_made,
                "tool_calls_made": run.tool_calls_made,
                "turns_used": run.turns_used,
                "fetched_ids": run.fetched_ids,
                "error": run.error,
            }
        )

    db.close()

    if not rows:
        raise SystemExit(
            f"L2 run produced no scorable questions: {len(unresolved)} question(s) "
            "had unresolvable gold. Nothing to report."
        )

    return {
        "dataset": str(dataset_path),
        "db": str(db_path),
        "model": getattr(client, "model", "fake"),
        "max_turns": max_turns,
        "n_questions": len(questions),
        "n_scored": len(rows),
        "n_gold_unresolved": len(unresolved),
        "gold_unresolved_ids": unresolved,
        "overall": aggregate(rows),
        "by_difficulty": slice_by(rows, "difficulty"),
        "by_pack": slice_by(rows, "pack"),
        "questions": rows,
        "runs": runs,
    }


def _print_summary(result: dict[str, Any], l1_baseline: dict[str, Any] | None) -> None:
    print(f"L2 reachability evaluation — {result['dataset']} — model={result['model']}")
    print(
        f"  {result['n_scored']}/{result['n_questions']} questions scored "
        f"({result['n_gold_unresolved']} gold unresolved)"
    )

    m = result["overall"]
    print(
        "\n  Overall (L2, model self-authored queries): "
        + "  ".join(f"{name}={m[name]:.3f}" for name in metric_names())
    )

    print("\n  By difficulty tier:")
    for tier, tm in result["by_difficulty"].items():
        metrics_str = "  ".join(f"{name}={tm[name]:.3f}" for name in metric_names())
        print(f"    {tier:20s} n={tm['n']:<4d} {metrics_str}")

    if l1_baseline is None:
        return

    l1_by_tier = l1_baseline.get("by_difficulty", {}).get("query", {})
    print("\n  reachability_gap = L1_recall@5 - L2_recall@5 (by tier):")
    for tier, tm in result["by_difficulty"].items():
        l1_tier = l1_by_tier.get(tier)
        if l1_tier is None:
            continue
        gap = l1_tier["recall@5"] - tm["recall@5"]
        print(
            f"    {tier:20s} L1={l1_tier['recall@5']:.3f}  L2={tm['recall@5']:.3f}  "
            f"gap={gap:+.3f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="L2: agent retrieval competence against a live model endpoint"
    )
    parser.add_argument("dataset_path", type=Path, help="Gold dataset JSON")
    parser.add_argument("--db", type=Path, required=True, help="Indexed synd DB")
    parser.add_argument(
        "--output", type=Path, required=True, help="Result JSON to write"
    )
    parser.add_argument(
        "--l1-baseline",
        type=Path,
        default=None,
        help="L1 baseline JSON (from l1_retrieval.py) to print reachability_gap against",
    )
    parser.add_argument("--max-turns", type=int, default=6)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only run the first N questions (for a quick smoke run)",
    )
    args = parser.parse_args()

    from tests.evals.model_client import client_from_env

    client = client_from_env()

    result = run_l2(
        args.dataset_path,
        args.db,
        client,
        max_turns=args.max_turns,
        question_limit=args.limit,
    )

    l1_baseline = None
    if args.l1_baseline is not None:
        with args.l1_baseline.open(encoding="utf-8") as fh:
            l1_baseline = json.load(fh)
    _print_summary(result, l1_baseline)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()
