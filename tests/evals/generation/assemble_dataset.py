"""Stage D: Assemble tiered questions into the standard eval dataset JSON.

Reads the Stage C output (tiered_<pack>.jsonl) and assembles it into the
dataset JSON format that the eval harness consumes. Deduplicates questions
by (source_url, heading_path, difficulty) to avoid near-identical entries,
and adds all rot-guard fields required by the validator.

Usage:
    python tests/evals/generation/assemble_dataset.py \\
        tests/evals/generation/work/tiered_mcp.jsonl \\
        tests/evals/generation/work/tiered_trigger.jsonl \\
        tests/evals/generation/work/tiered_resend.jsonl \\
        --output tests/evals/datasets/real/pilot_v1.json \\
        --corpus-version 2025-05-29

Output format (compatible with tests/evals/datasets/ schema):
    {
      "schema_version": 2,
      "description": "...",
      "corpus": {"type": "real", "version": "2025-05-29", "packs": [...]},
      "questions": [
        {
          "id": "r001",
          "pack": "mcp",
          "difficulty": "paraphrase",
          "query": "...",
          "keyword_query": "...",
          "gold": [{
            "source_url": "...",
            "heading_path": "...",
            "content_hash": "sha256:...",
            "anchor": "first 80 chars of chunk content"
          }]
        }
      ]
    }
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _dedupe_key(rec: dict) -> tuple:
    """Deduplication key: same gold chunk + same difficulty → keep one query."""
    return (rec["source_url"], rec["heading_path"], rec["difficulty"])


def assemble(
    tiered_paths: list[Path],
    output_path: Path,
    corpus_version: str,
) -> None:
    seen_keys: dict[tuple, dict] = {}
    packs_seen: set[str] = set()

    for tiered_path in tiered_paths:
        if not tiered_path.exists():
            print(f"WARNING: {tiered_path} not found, skipping")
            continue
        with tiered_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                packs_seen.add(rec["pack_name"])
                key = _dedupe_key(rec)
                if key not in seen_keys:
                    seen_keys[key] = rec
                else:
                    # Prefer vocab_mismatch > paraphrase > direct (harder is rarer)
                    existing = seen_keys[key]
                    tier_priority = {
                        "vocabulary_mismatch": 2,
                        "paraphrase": 1,
                        "direct": 0,
                    }
                    if tier_priority.get(rec["difficulty"], 0) > tier_priority.get(
                        existing["difficulty"], 0
                    ):
                        seen_keys[key] = rec

    questions = []
    for idx, rec in enumerate(seen_keys.values(), 1):
        q_id = f"r{idx:04d}"
        question = {
            "id": q_id,
            "pack": rec["pack_name"],
            "difficulty": rec["difficulty"],
            "query": rec["nl_query"],
            "keyword_query": rec["keyword_query"],
            "gold": [
                {
                    "source_url": rec["source_url"],
                    "heading_path": rec["heading_path"],
                    "content_hash": rec["content_hash"],
                    "anchor": rec["anchor"],
                }
            ],
            "_meta": {
                "capability": rec["capability"],
                "model": rec["model"],
                "persona": rec["persona"],
                "jaccard": rec.get("jaccard"),
                "kw_rank": rec.get("kw_rank"),
                "nl_rank": rec.get("nl_rank"),
            },
        }
        questions.append(question)

    tier_counts: dict[str, int] = {}
    for q in questions:
        tier_counts[q["difficulty"]] = tier_counts.get(q["difficulty"], 0) + 1

    dataset = {
        "schema_version": 2,
        "description": (
            f"Gold retrieval questions for scaled eval harness. "
            f"Generated via dual-model pipeline (Stage A-D). "
            f"Corpus version: {corpus_version}. "
            f"Tiers: {tier_counts}. "
            f"Packs: {sorted(packs_seen)}."
        ),
        "corpus": {
            "type": "real",
            "version": corpus_version,
            "packs": sorted(packs_seen),
            "build_command": (
                "python scripts/build_pilot_packs.py  "
                "# then: synd add <pack>.ctx --db <db_path>"
            ),
        },
        "questions": questions,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(dataset, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    print(
        f"Assembled {len(questions)} questions from {len(packs_seen)} packs "
        f"→ {output_path}"
    )
    for tier, count in sorted(tier_counts.items()):
        print(f"  {tier}: {count}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage D: assemble dataset JSON")
    parser.add_argument(
        "tiered_paths",
        nargs="+",
        type=Path,
        help="One or more Stage C output JSONL files",
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="Output dataset JSON"
    )
    parser.add_argument(
        "--corpus-version",
        default="2025-05-29",
        help="Corpus version string (default: 2025-05-29)",
    )
    args = parser.parse_args()
    assemble(args.tiered_paths, args.output, args.corpus_version)


if __name__ == "__main__":
    main()
