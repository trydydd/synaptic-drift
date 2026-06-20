"""Stage A: Extract capability statements from chunks using Sonnet.

Reads chunks from the JSONL produced by extract_chunks.py and calls the
Anthropic Messages API for each one, writing a capability statement per chunk.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python tests/evals/generation/generate_stage_a.py \\
        tests/evals/generation/work/chunks_mcp.jsonl \\
        --output tests/evals/generation/work/capabilities_mcp.jsonl \\
        [--resume]  # skip already-processed chunk_ids in output file

Output JSONL (one line per chunk):
    {
        "pack_name": "mcp",
        "chunk_id": 17,
        "heading_path": "...",
        "source_url": "...",
        "content_hash": "sha256:...",
        "capability": "how to mark a tool as safe to call more than once"
    }

Rate: ~1 req/s; 80 chunks ≈ 2-3 minutes. Costs < $0.10 for a pilot run.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path


_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 80
_PROMPT_TEMPLATE = Path(__file__).parent / "prompts" / "stage_a.txt"


def _load_existing_ids(output_path: Path) -> set[tuple[str, int]]:
    ids: set[tuple[str, int]] = set()
    if not output_path.exists():
        return ids
    with output_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                ids.add((rec["pack_name"], rec["chunk_id"]))
            except (json.JSONDecodeError, KeyError):
                pass
    return ids


def _call_anthropic(prompt_text: str, api_key: str) -> str:
    payload = {
        "model": _MODEL,
        "max_tokens": _MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt_text}],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        _ANTHROPIC_URL,
        data=data,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body["content"][0]["text"].strip()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Anthropic API error {exc.code}: {body}") from exc


def generate_stage_a(
    chunks_path: Path,
    output_path: Path,
    resume: bool,
    delay_s: float,
) -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY not set")

    prompt_template = _PROMPT_TEMPLATE.read_text(encoding="utf-8")
    skip_ids = _load_existing_ids(output_path) if resume else set()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if resume else "w"

    with (
        chunks_path.open(encoding="utf-8") as inp,
        output_path.open(mode, encoding="utf-8") as out,
    ):
        for line_no, line in enumerate(inp, 1):
            line = line.strip()
            if not line:
                continue
            chunk = json.loads(line)
            key = (chunk["pack_name"], chunk["chunk_id"])

            if key in skip_ids:
                print(f"  skip  chunk {chunk['chunk_id']} (already done)")
                continue

            prompt = prompt_template.format(
                source_url=chunk["source_url"],
                heading_path=chunk["heading_path"],
                content=chunk["content"],
            )

            print(f"  A [{line_no}] chunk {chunk['chunk_id']} {chunk['heading_path'][:60]}")
            capability = _call_anthropic(prompt, api_key)
            print(f"       → {capability[:90]}")

            record = {
                "pack_name": chunk["pack_name"],
                "chunk_id": chunk["chunk_id"],
                "heading_path": chunk["heading_path"],
                "source_url": chunk["source_url"],
                "content_hash": chunk["content_hash"],
                "capability": capability,
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()

            if delay_s > 0:
                time.sleep(delay_s)


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage A: capability extraction")
    parser.add_argument("chunks_path", type=Path, help="Input chunks JSONL")
    parser.add_argument("--output", type=Path, required=True, help="Output JSONL")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip chunks already present in output file",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Seconds between API calls (default 0.5)",
    )
    args = parser.parse_args()
    generate_stage_a(args.chunks_path, args.output, args.resume, args.delay)
    print("Stage A complete.")


if __name__ == "__main__":
    main()
