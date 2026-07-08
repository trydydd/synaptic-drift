"""Finalize Stage B (strong half): validate session-authored queries, merge.

The strong-model half of Stage B (personas: expert, paraphrase) is authored by
Claude in a FRESH Claude Code session that reads only session_a_<pack>.jsonl
(pack_name + chunk_id + capability) and prompts/stage_b_claude.txt — never the
chunk text, heading paths, or URLs. See docs/pilot-run-guide.md. The session
writes one record per (chunk, persona):

    {"pack_name": "mcp", "chunk_id": 17, "persona": "expert",
     "nl_query": "how do i mark a tool as retry-safe",
     "keyword_query": "tool retry safe"}

This script validates the session output, joins metadata + capability from the
canonical capabilities JSONL, and writes full raw-query records with model
label "claude-session". If the output file already exists it appends, skipping
(pack_name, chunk_id, model) keys already present — so it composes with
generate_stage_b.py (the vLLM weak half) run in either order.

Usage:
    python tests/evals/generation/finalize_stage_b.py \\
        --capabilities tests/evals/generation/work/capabilities_mcp.jsonl \\
        --session      tests/evals/generation/work/session_b_mcp.jsonl \\
        --output       tests/evals/generation/work/raw_queries_mcp.jsonl

Exit 0: all records valid, output written/appended.
Exit 1: failures listed on stderr, output untouched. Fix the session file
and re-run.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_MODEL_LABEL = "claude-session"
_PERSONAS = ("expert", "paraphrase")
_KEYWORD_MIN_TERMS = 2
_KEYWORD_MAX_TERMS = 4

# Small stopword set for the cross-persona vocabulary-overlap warning only.
_STOPWORDS = frozenset(
    "a an and are as at be but by can do for from how i in is it my of on or "
    "s set setting that the this to up use using want what when where with you".split()
)


def _read_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{line_no}: invalid JSON: {exc}") from exc
    return records


def _load_done_keys(output_path: Path) -> set[tuple[str, int, str]]:
    """Return set of (pack_name, chunk_id, model) already in output."""
    done: set[tuple[str, int, str]] = set()
    if not output_path.exists():
        return done
    with output_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                done.add((rec["pack_name"], rec["chunk_id"], rec["model"]))
            except (json.JSONDecodeError, KeyError):
                pass
    return done


def _content_terms(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOPWORDS}


def _validate_queries(rec: dict) -> list[str]:
    """Return failures for one session record's query fields."""
    failures: list[str] = []
    nl = rec.get("nl_query")
    kw = rec.get("keyword_query")

    if not isinstance(nl, str) or not nl.strip():
        failures.append("nl_query is empty or not a string")
    elif "\n" in nl.strip():
        failures.append("nl_query spans multiple lines")

    if not isinstance(kw, str) or not kw.strip():
        failures.append("keyword_query is empty or not a string")
    else:
        n_terms = len(kw.split())
        if n_terms < _KEYWORD_MIN_TERMS or n_terms > _KEYWORD_MAX_TERMS:
            failures.append(
                f"keyword_query has {n_terms} terms (expected {_KEYWORD_MIN_TERMS}-{_KEYWORD_MAX_TERMS})"
            )
    return failures


def finalize_stage_b(
    capabilities_path: Path, session_path: Path, output_path: Path
) -> int:
    capabilities = _read_jsonl(capabilities_path)
    session = _read_jsonl(session_path)

    cap_keys = [(c["pack_name"], c["chunk_id"]) for c in capabilities]
    caps_by_key = dict(zip(cap_keys, capabilities))

    failures: list[str] = []
    warnings: list[str] = []
    session_by_key: dict[tuple[str, int, str], dict] = {}

    for rec in session:
        try:
            key = (rec["pack_name"], rec["chunk_id"], rec["persona"])
        except (KeyError, TypeError):
            failures.append(
                f"session record missing pack_name/chunk_id/persona: {json.dumps(rec)[:120]}"
            )
            continue
        if (key[0], key[1]) not in caps_by_key:
            failures.append(f"chunk {key[:2]} not present in {capabilities_path.name}")
            continue
        if key[2] not in _PERSONAS:
            failures.append(
                f"chunk {key[:2]}: unknown persona {key[2]!r} (expected one of {_PERSONAS})"
            )
            continue
        if key in session_by_key:
            failures.append(
                f"chunk {key[:2]}: persona {key[2]!r} appears more than once"
            )
            continue
        session_by_key[key] = rec
        failures.extend(
            f"chunk {key[:2]} [{key[2]}]: {msg}" for msg in _validate_queries(rec)
        )

    for cap_key in cap_keys:
        for persona in _PERSONAS:
            if (cap_key[0], cap_key[1], persona) not in session_by_key:
                failures.append(
                    f"chunk {cap_key}: no {persona!r} record in session output"
                )

    # Warn (not fail) when the paraphrase persona reuses the expert persona's
    # vocabulary — the prompt forbids it, but generic terms legitimately overlap.
    for cap_key in cap_keys:
        expert = session_by_key.get((cap_key[0], cap_key[1], "expert"))
        para = session_by_key.get((cap_key[0], cap_key[1], "paraphrase"))
        if not expert or not para:
            continue
        expert_terms = _content_terms(
            f"{expert.get('nl_query', '')} {expert.get('keyword_query', '')}"
        )
        para_terms = _content_terms(
            f"{para.get('nl_query', '')} {para.get('keyword_query', '')}"
        )
        shared = sorted(expert_terms & para_terms)
        if shared:
            warnings.append(
                f"chunk {cap_key}: paraphrase shares terms with expert: {', '.join(shared)}"
            )

    for msg in warnings:
        print(f"  WARN  {msg}")
    for msg in failures:
        print(f"  FAIL  {msg}", file=sys.stderr)

    if failures:
        print(
            f"\nStage B validation failed: {len(failures)} failure(s), "
            f"{len(warnings)} warning(s). Output untouched.",
            file=sys.stderr,
        )
        return 1

    done_keys = _load_done_keys(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    skipped = 0
    with output_path.open("a", encoding="utf-8") as out:
        for cap_key in cap_keys:
            if (cap_key[0], cap_key[1], _MODEL_LABEL) in done_keys:
                skipped += 1
                continue
            cap = caps_by_key[cap_key]
            for persona in _PERSONAS:
                rec = session_by_key[(cap_key[0], cap_key[1], persona)]
                record = {
                    "pack_name": cap["pack_name"],
                    "chunk_id": cap["chunk_id"],
                    "heading_path": cap["heading_path"],
                    "source_url": cap["source_url"],
                    "content_hash": cap["content_hash"],
                    "capability": cap["capability"],
                    "model": _MODEL_LABEL,
                    "persona": persona,
                    "nl_query": rec["nl_query"].strip(),
                    "keyword_query": rec["keyword_query"].strip(),
                }
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                written += 1

    print(
        f"\nStage B (strong half) finalized: {written} records → {output_path} "
        f"({skipped} chunk(s) already present, {len(warnings)} warning(s))"
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Finalize Stage B strong half: validate and merge"
    )
    parser.add_argument(
        "--capabilities", type=Path, required=True, help="Canonical capabilities JSONL"
    )
    parser.add_argument(
        "--session", type=Path, required=True, help="Session-authored queries JSONL"
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Raw queries JSONL (appended if it exists)",
    )
    args = parser.parse_args()
    raise SystemExit(finalize_stage_b(args.capabilities, args.session, args.output))


if __name__ == "__main__":
    main()
