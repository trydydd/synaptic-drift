"""Extract a labeled sample of chunks from a .ctx pack for gold generation.

Usage:
    python tests/evals/generation/extract_chunks.py \\
        tests/benchmarks/fixtures/packs/mcp@2025-05-29.ctx \\
        --sample 80 --seed 42 \\
        --output tests/evals/generation/work/chunks_mcp.jsonl

Each output line is a JSON object:
    {
        "pack_name": "mcp",
        "chunk_id": 17,
        "heading_path": "mcp/tools / Tools / Tool Annotations",
        "source_url": "https://modelcontextprotocol.io/...",
        "content_hash": "sha256:...",
        "content": "..."   # truncated to 2000 chars
    }

Run once per pack; the output is the input to Stage A, which is authored by
Claude in a Claude Code session and finalized by finalize_stage_a.py (see
docs/pilot-run-guide.md).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import zipfile
from pathlib import Path


_CONTENT_TRUNCATE = 2000
_MIN_CONTENT_LEN = 100


def _pack_name_from_path(path: Path) -> str:
    """Extract pack name from filename like mcp@2025-05-29.ctx → mcp."""
    stem = path.stem
    return stem.split("@")[0] if "@" in stem else stem


def _should_include(chunk: dict) -> bool:
    """Filter out very short or boilerplate chunks."""
    content = chunk.get("content") or ""
    if len(content) < _MIN_CONTENT_LEN:
        return False
    heading = chunk.get("heading_path") or ""
    # Skip pure navigation / index chunks
    skip_patterns = [
        r"(?i)\bchangelog\b",
        r"(?i)\bindex\b$",
        r"(?i)\brelease notes\b",
    ]
    for pat in skip_patterns:
        if re.search(pat, heading):
            return False
    return True


def extract_chunks(
    ctx_path: Path,
    sample: int | None,
    seed: int,
    output: Path,
) -> None:
    pack_name = _pack_name_from_path(ctx_path)

    with zipfile.ZipFile(ctx_path, "r") as zf:
        raw = zf.read("chunks.jsonl").decode("utf-8")

    chunks: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if _should_include(record):
            chunks.append(record)

    if sample is not None and sample < len(chunks):
        rng = random.Random(seed)
        chunks = rng.sample(chunks, sample)
        chunks.sort(key=lambda c: c.get("id", 0))

    output.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with output.open("w", encoding="utf-8") as fh:
        for c in chunks:
            content_full = c.get("content") or ""
            content_truncated = content_full[:_CONTENT_TRUNCATE]
            content_hash = c.get("content_hash") or (
                "sha256:" + hashlib.sha256(content_full.encode()).hexdigest()
            )
            record = {
                "pack_name": pack_name,
                "chunk_id": c["id"],
                "heading_path": c.get("heading_path") or "",
                "source_url": c.get("source_url") or "",
                "content_hash": content_hash,
                "content": content_truncated,
            }
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1

    print(f"Wrote {written} chunks from '{pack_name}' → {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("ctx_path", type=Path, help=".ctx pack file")
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="Random sample size (default: all eligible chunks)",
    )
    parser.add_argument("--seed", type=int, default=42, help="RNG seed for sampling")
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output JSONL path",
    )
    args = parser.parse_args()
    extract_chunks(args.ctx_path, args.sample, args.seed, args.output)


if __name__ == "__main__":
    main()
