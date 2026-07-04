"""Build the three pilot packs for the scaled eval harness.

Downloads llms-full.txt from mcp, trigger, and resend — chosen because they
span AI/ML, devtools, and comms/API domains — and builds .ctx packs into
tests/evals/generation/work/packs/. Then imports them into a pilot DB at
tests/evals/generation/work/pilot.db.

Run:
    python scripts/build_pilot_packs.py

Requirements: synd CLI in .venv/bin/synd or PATH.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

VERSION = "2025-05-29"

PACK_DIR = Path("tests/evals/generation/work/packs")
DB_PATH = Path("tests/evals/generation/work/pilot.db")

PILOT_PACKS: list[tuple[str, str]] = [
    ("mcp", "https://modelcontextprotocol.io/llms-full.txt"),
    ("trigger", "https://trigger.dev/docs/llms-full.txt"),
    ("resend", "https://resend.com/docs/llms-full.txt"),
]


def _find_synd() -> str:
    for c in [".venv/bin/synd", "synd"]:
        try:
            subprocess.run([c, "--help"], capture_output=True)
            return c
        except FileNotFoundError:
            pass
    print("error: synd not found. Run: pip install -e '.[all]'", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    PACK_DIR.mkdir(parents=True, exist_ok=True)
    synd = _find_synd()

    built: list[Path] = []

    for name, url in PILOT_PACKS:
        pack_path = PACK_DIR / f"{name}@{VERSION}.ctx"
        if pack_path.exists():
            print(f"skip  {name:<12} (already exists: {pack_path})")
            built.append(pack_path)
            continue

        print(f"build {name:<12} {url}")
        result = subprocess.run(
            [
                synd,
                "build",
                f"{name}@{VERSION}",
                "--source",
                url,
                "--output",
                str(PACK_DIR),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and pack_path.exists():
            print(f"  -> {pack_path}")
            built.append(pack_path)
        else:
            err = (result.stderr or result.stdout or "").strip().splitlines()[-1:]
            print(f"  -> FAILED: {err}")

    print(f"\nImporting {len(built)} packs into {DB_PATH}")
    # `synd add` has no --db flag: it always writes to .synd/index.db relative
    # to cwd. Import with cwd set to the work dir, then move that index.db to
    # DB_PATH so it matches the path the rest of the pipeline expects.
    synd_dir = DB_PATH.parent / ".synd"
    for pack_path in built:
        print(f"  add {pack_path.name}")
        result = subprocess.run(
            [Path(synd).resolve().as_posix(), "add", str(pack_path.resolve())],
            capture_output=True,
            text=True,
            cwd=DB_PATH.parent,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip().splitlines()[-1:]
            print(f"    FAILED: {err}")
        else:
            print("    ok")

    built_index = synd_dir / "index.db"
    if built_index.exists():
        built_index.replace(DB_PATH)
        for extra in ("index.db-wal", "index.db-shm"):
            extra_path = synd_dir / extra
            if extra_path.exists():
                extra_path.unlink()

    print(f"\nDone. Pilot DB: {DB_PATH}")
    print(
        "\nNext steps:\n"
        "  1. python tests/evals/generation/extract_chunks.py \\\n"
        "       tests/evals/generation/work/packs/mcp@2025-05-29.ctx \\\n"
        "       --sample 80 --output tests/evals/generation/work/chunks_mcp.jsonl\n"
        "  2. (repeat extract_chunks for trigger and resend)\n"
        "  3. export ANTHROPIC_API_KEY=...\n"
        "     python tests/evals/generation/generate_stage_a.py \\\n"
        "       tests/evals/generation/work/chunks_mcp.jsonl \\\n"
        "       --output tests/evals/generation/work/capabilities_mcp.jsonl\n"
        "  4. export SYND_GEN_VLLM_URL=http://192.168.0.214:8000/v1\n"
        "     export SYND_GEN_VLLM_MODEL=<served-model-name>\n"
        "     python tests/evals/generation/generate_stage_b.py \\\n"
        "       tests/evals/generation/work/capabilities_mcp.jsonl \\\n"
        "       --output tests/evals/generation/work/raw_queries_mcp.jsonl\n"
        "  5. python tests/evals/generation/stage_c_tier.py \\\n"
        "       --raw-queries tests/evals/generation/work/raw_queries_mcp.jsonl \\\n"
        "       --chunks tests/evals/generation/work/chunks_mcp.jsonl \\\n"
        "       --db tests/evals/generation/work/pilot.db \\\n"
        "       --pack mcp \\\n"
        "       --output tests/evals/generation/work/tiered_mcp.jsonl\n"
        "  6. (repeat stages 3-5 for trigger and resend)\n"
        "  7. python tests/evals/generation/assemble_dataset.py \\\n"
        "       tests/evals/generation/work/tiered_mcp.jsonl \\\n"
        "       tests/evals/generation/work/tiered_trigger.jsonl \\\n"
        "       tests/evals/generation/work/tiered_resend.jsonl \\\n"
        "       --output tests/evals/datasets/real/pilot_v1.json\n"
        "  8. python tests/evals/generation/validate_rot_guard.py \\\n"
        "       tests/evals/datasets/real/pilot_v1.json \\\n"
        "       --db tests/evals/generation/work/pilot.db\n"
    )


if __name__ == "__main__":
    main()
