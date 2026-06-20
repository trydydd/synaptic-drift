"""Rot-guard validator: verify dataset integrity against the live corpus.

Checks every gold entry in a dataset JSON against the indexed DB:
1. The (source_url, heading_path) pair resolves to at least one chunk.
2. The chunk's content_hash matches the stored hash.
3. The anchor substring appears in the chunk content.

Run this after corpus rebuilds to catch upstream doc drift.

Usage:
    python tests/evals/generation/validate_rot_guard.py \\
        tests/evals/datasets/real/pilot_v1.json \\
        --db tests/evals/generation/work/pilot.db

Exit 0 if all pass. Exit 1 if any fail (lists failures).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from synd.storage.db import Database


def _find_chunk(
    db: Database,
    source_url: str,
    heading_path: str,
    pack_name: str | None,
) -> dict | None:
    """Find a chunk by source_url suffix + heading_path. Returns first match."""
    params: list = []
    where_clauses = ["c.heading_path = ?"]
    params.append(heading_path)

    # source_url matching: match by suffix (allows version-prefix drift)
    if source_url:
        # Try exact first, then suffix
        row = db.conn.execute(
            "SELECT c.id, c.content_hash, c.content FROM chunks c "
            "WHERE c.heading_path = ? AND c.source_url = ?",
            (heading_path, source_url),
        ).fetchone()
        if row:
            return dict(row)
        # Suffix match (last path segment)
        suffix = "/" + source_url.lstrip("/").split("//", 1)[-1].split("/", 1)[-1]
        suffix = "%" + suffix.replace("%", "%%")
        row = db.conn.execute(
            "SELECT c.id, c.content_hash, c.content FROM chunks c "
            "WHERE c.heading_path = ? AND c.source_url LIKE ?",
            (heading_path, suffix),
        ).fetchone()
        if row:
            return dict(row)

    # Fall back to heading_path only
    where = " AND ".join(where_clauses)
    if pack_name:
        where += " AND c.package = ?"
        params.append(pack_name)
    row = db.conn.execute(
        f"SELECT c.id, c.content_hash, c.content FROM chunks c WHERE {where} LIMIT 1",
        params,
    ).fetchone()
    return dict(row) if row else None


def validate(dataset_path: Path, db_path: Path) -> bool:
    with dataset_path.open(encoding="utf-8") as fh:
        dataset = json.load(fh)

    db = Database(db_path)
    failures: list[str] = []
    checked = 0

    for q in dataset.get("questions", []):
        q_id = q["id"]
        pack = q.get("pack")
        for gold in q.get("gold", []):
            source_url = gold.get("source_url", "")
            heading_path = gold.get("heading_path", "")
            expected_hash = gold.get("content_hash", "")
            anchor = gold.get("anchor", "")

            chunk = _find_chunk(db, source_url, heading_path, pack)
            if chunk is None:
                failures.append(
                    f"  [{q_id}] MISSING: heading_path={heading_path!r}"
                )
                checked += 1
                continue

            actual_hash = chunk.get("content_hash") or ""
            if expected_hash and actual_hash and actual_hash != expected_hash:
                failures.append(
                    f"  [{q_id}] HASH MISMATCH: {heading_path!r}\n"
                    f"    expected: {expected_hash}\n"
                    f"    actual:   {actual_hash}"
                )

            content = chunk.get("content") or ""
            if anchor and anchor not in content:
                failures.append(
                    f"  [{q_id}] ANCHOR DRIFT: {heading_path!r}\n"
                    f"    anchor not found: {anchor!r}"
                )

            checked += 1

    db.close()

    if failures:
        print(f"ROT-GUARD FAILED: {len(failures)}/{checked} gold entries degraded:")
        for msg in failures:
            print(msg)
        return False

    print(f"ROT-GUARD OK: {checked} gold entries verified against {db_path}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate dataset rot-guard")
    parser.add_argument("dataset_path", type=Path)
    parser.add_argument("--db", type=Path, required=True, help="Pilot synd DB")
    args = parser.parse_args()
    ok = validate(args.dataset_path, args.db)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
