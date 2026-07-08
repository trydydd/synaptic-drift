"""Finalize Stage A: validate session-authored capabilities, join chunk metadata.

Stage A (capability extraction) is authored by Claude inside a Claude Code
session — see docs/pilot-run-guide.md — instead of via Anthropic API calls.
The session reads chunks_<pack>.jsonl plus prompts/stage_a.txt and writes a
minimal JSONL, one record per chunk:

    {"pack_name": "mcp", "chunk_id": 17, "capability": "how to ..."}

This script is the mechanical half. It validates the session output (coverage
and prompt-rule conformance), joins heading_path / source_url / content_hash
from the chunks file, and writes the canonical capabilities JSONL that Stage B
consumes. The session never transcribes metadata, so transcription errors are
structurally impossible.

Usage:
    python tests/evals/generation/finalize_stage_a.py \\
        --chunks  tests/evals/generation/work/chunks_mcp.jsonl \\
        --session tests/evals/generation/work/session_a_mcp.jsonl \\
        --output  tests/evals/generation/work/capabilities_mcp.jsonl

Exit 0: all records valid, output written (chunks-file order, deterministic).
Exit 1: failures listed on stderr, output not written. Fix the session file
and re-run.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_ALLOWED_PREFIXES = ("how to", "when to", "configure", "understand", "choose between")
# The prompt asks for 10-30 words; enforce with a small tolerance band so a
# near-miss is a warning, not a re-authoring loop. Outside the hard band it fails.
_HARD_MIN_WORDS = 8
_HARD_MAX_WORDS = 35
_SOFT_MIN_WORDS = 10
_SOFT_MAX_WORDS = 30


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


def _validate_capability(
    pack_name: str, capability: object
) -> tuple[list[str], list[str]]:
    """Return (failures, warnings) for one capability statement."""
    failures: list[str] = []
    warnings: list[str] = []

    if not isinstance(capability, str) or not capability.strip():
        return (["capability is empty or not a string"], [])
    if "\n" in capability.strip():
        failures.append("capability spans multiple lines")

    text = capability.strip()
    words = text.split()
    if len(words) < _HARD_MIN_WORDS or len(words) > _HARD_MAX_WORDS:
        failures.append(
            f"capability is {len(words)} words (hard bounds {_HARD_MIN_WORDS}-{_HARD_MAX_WORDS})"
        )
    elif len(words) < _SOFT_MIN_WORDS or len(words) > _SOFT_MAX_WORDS:
        warnings.append(
            f"capability is {len(words)} words (prompt asks for {_SOFT_MIN_WORDS}-{_SOFT_MAX_WORDS})"
        )

    if not text.lower().startswith(_ALLOWED_PREFIXES):
        failures.append(
            f"capability must start with one of {', '.join(repr(p) for p in _ALLOWED_PREFIXES)}"
        )

    if re.search(rf"\b{re.escape(pack_name)}\b", text, re.IGNORECASE):
        failures.append(
            f"capability mentions the pack name {pack_name!r} (vocabulary leak)"
        )

    return (failures, warnings)


def finalize_stage_a(chunks_path: Path, session_path: Path, output_path: Path) -> int:
    chunks = _read_jsonl(chunks_path)
    session = _read_jsonl(session_path)

    chunk_keys = [(c["pack_name"], c["chunk_id"]) for c in chunks]
    chunks_by_key = dict(zip(chunk_keys, chunks))

    failures: list[str] = []
    warnings: list[str] = []
    session_by_key: dict[tuple[str, int], dict] = {}

    for rec in session:
        try:
            key = (rec["pack_name"], rec["chunk_id"])
        except (KeyError, TypeError):
            failures.append(
                f"session record missing pack_name/chunk_id: {json.dumps(rec)[:120]}"
            )
            continue
        if key not in chunks_by_key:
            failures.append(f"chunk {key} not present in {chunks_path.name}")
            continue
        if key in session_by_key:
            failures.append(f"chunk {key} appears more than once in session output")
            continue
        session_by_key[key] = rec

        rec_failures, rec_warnings = _validate_capability(key[0], rec.get("capability"))
        failures.extend(f"chunk {key}: {msg}" for msg in rec_failures)
        warnings.extend(f"chunk {key}: {msg}" for msg in rec_warnings)

    missing = [key for key in chunk_keys if key not in session_by_key]
    failures.extend(f"chunk {key}: no capability in session output" for key in missing)

    for msg in warnings:
        print(f"  WARN  {msg}")
    for msg in failures:
        print(f"  FAIL  {msg}", file=sys.stderr)

    if failures:
        print(
            f"\nStage A validation failed: {len(failures)} failure(s), "
            f"{len(warnings)} warning(s). Output not written.",
            file=sys.stderr,
        )
        return 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as out:
        for key in chunk_keys:
            chunk = chunks_by_key[key]
            record = {
                "pack_name": chunk["pack_name"],
                "chunk_id": chunk["chunk_id"],
                "heading_path": chunk["heading_path"],
                "source_url": chunk["source_url"],
                "content_hash": chunk["content_hash"],
                "capability": session_by_key[key]["capability"].strip(),
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(
        f"\nStage A finalized: {len(chunk_keys)} capabilities → {output_path} ({len(warnings)} warning(s))"
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Finalize Stage A: validate and join metadata"
    )
    parser.add_argument(
        "--chunks", type=Path, required=True, help="Chunks JSONL from extract_chunks.py"
    )
    parser.add_argument(
        "--session",
        type=Path,
        required=True,
        help="Session-authored capabilities JSONL",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Canonical capabilities JSONL to write",
    )
    args = parser.parse_args()
    raise SystemExit(finalize_stage_a(args.chunks, args.session, args.output))


if __name__ == "__main__":
    main()
