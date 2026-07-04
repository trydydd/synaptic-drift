# Scaled Eval Harness — Pilot Run Guide

**Branch**: `claude/local-first-context-review-fzlvng`
**Corpus**: mcp · trigger · resend (3 pilot packs, ~100K-chunk index)
**Output**: `tests/evals/datasets/real/pilot_v1.json` (commit this when done)

---

## Context

This is the generation pipeline for the scaled retrieval-quality eval harness
described in `docs/eval-harness-design.md`. The skeleton is committed; this
guide runs it end-to-end on your machine.

The pipeline has four stages:

| Stage | What | Model |
|---|---|---|
| A | Chunk → neutral capability statement | Sonnet 4.6 (API) |
| B | Capability → persona query pairs | Sonnet + Qwen3-35B-A3B (vLLM) |
| C | Measure actual retrieval difficulty | Model-free (Jaccard + FTS5 rank) |
| D | Assemble + rot-guard → dataset JSON | Model-free |

Work products go in `tests/evals/generation/work/` (gitignored).

---

## Prerequisites

- `synd` CLI: `.venv/bin/synd` or `synd` in PATH (`pip install -e '.[all]'`)
- `ANTHROPIC_API_KEY` — for Stage A and the Sonnet half of Stage B
- vLLM serving Qwen3-35B-A3B locally — for the 35B half of Stage B
- Network access to modelcontextprotocol.io, trigger.dev, resend.com

---

## Step 1 — Build pilot corpus

Downloads mcp, trigger, resend packs and imports them into a pilot DB.

```bash
python scripts/build_pilot_packs.py
```

Writes to:
- `tests/evals/generation/work/packs/*.ctx`
- `tests/evals/generation/work/pilot.db`

Takes ~5–10 minutes. Already-built packs are skipped automatically.

---

## Step 2 — Extract chunks

Sample chunks from each pack for labeling.

```bash
python tests/evals/generation/extract_chunks.py \
  tests/evals/generation/work/packs/mcp@2025-05-29.ctx \
  --sample 80 --seed 42 \
  --output tests/evals/generation/work/chunks_mcp.jsonl

python tests/evals/generation/extract_chunks.py \
  tests/evals/generation/work/packs/trigger@2025-05-29.ctx \
  --sample 80 --seed 42 \
  --output tests/evals/generation/work/chunks_trigger.jsonl

python tests/evals/generation/extract_chunks.py \
  tests/evals/generation/work/packs/resend@2025-05-29.ctx \
  --sample 80 --seed 42 \
  --output tests/evals/generation/work/chunks_resend.jsonl
```

---

## Step 3 — Stage A: capability extraction (Sonnet)

One API call per chunk. ~2–3 minutes per pack. Costs < $0.10 for 80 chunks.

```bash
export ANTHROPIC_API_KEY=<your-key>

python tests/evals/generation/generate_stage_a.py \
  tests/evals/generation/work/chunks_mcp.jsonl \
  --output tests/evals/generation/work/capabilities_mcp.jsonl

python tests/evals/generation/generate_stage_a.py \
  tests/evals/generation/work/chunks_trigger.jsonl \
  --output tests/evals/generation/work/capabilities_trigger.jsonl

python tests/evals/generation/generate_stage_a.py \
  tests/evals/generation/work/chunks_resend.jsonl \
  --output tests/evals/generation/work/capabilities_resend.jsonl
```

Add `--resume` to any command to skip already-processed chunks if interrupted.

---

## Step 4 — Stage B: query synthesis (Sonnet + 35B-A3B)

Two models per chunk: Sonnet generates expert + paraphrase queries; 35B generates
vocabulary-mismatch + hurried-user queries.

```bash
export ANTHROPIC_API_KEY=<your-key>
export SYND_GEN_VLLM_URL=http://192.168.0.214:8000/v1
export SYND_GEN_VLLM_MODEL=<name from /v1/models>
# export SYND_GEN_VLLM_API_KEY=<token>   # only if your vLLM requires auth

python tests/evals/generation/generate_stage_b.py \
  tests/evals/generation/work/capabilities_mcp.jsonl \
  --output tests/evals/generation/work/raw_queries_mcp.jsonl

python tests/evals/generation/generate_stage_b.py \
  tests/evals/generation/work/capabilities_trigger.jsonl \
  --output tests/evals/generation/work/raw_queries_trigger.jsonl

python tests/evals/generation/generate_stage_b.py \
  tests/evals/generation/work/capabilities_resend.jsonl \
  --output tests/evals/generation/work/raw_queries_resend.jsonl
```

**Flags**:
- `--only-sonnet` — skip 35B (run Sonnet first, then come back for 35B)
- `--only-35b` — skip Sonnet
- `--resume` — skip chunks already in the output file

---

## Step 5 — Stage C: measured tiering (model-free)

Assigns `direct` / `paraphrase` / `vocabulary_mismatch` based on Jaccard overlap
and actual FTS5 rank. Drops questions where gold is unreachable.

```bash
python tests/evals/generation/stage_c_tier.py \
  --raw-queries tests/evals/generation/work/raw_queries_mcp.jsonl \
  --chunks      tests/evals/generation/work/chunks_mcp.jsonl \
  --db          tests/evals/generation/work/pilot.db \
  --pack mcp \
  --output tests/evals/generation/work/tiered_mcp.jsonl

python tests/evals/generation/stage_c_tier.py \
  --raw-queries tests/evals/generation/work/raw_queries_trigger.jsonl \
  --chunks      tests/evals/generation/work/chunks_trigger.jsonl \
  --db          tests/evals/generation/work/pilot.db \
  --pack trigger \
  --output tests/evals/generation/work/tiered_trigger.jsonl

python tests/evals/generation/stage_c_tier.py \
  --raw-queries tests/evals/generation/work/raw_queries_resend.jsonl \
  --chunks      tests/evals/generation/work/chunks_resend.jsonl \
  --db          tests/evals/generation/work/pilot.db \
  --pack resend \
  --output tests/evals/generation/work/tiered_resend.jsonl
```

Prints `KEEP` / `DROP` per question with the tier, Jaccard score, and FTS5 rank.

---

## Step 6 — Assemble + validate

```bash
python tests/evals/generation/assemble_dataset.py \
  tests/evals/generation/work/tiered_mcp.jsonl \
  tests/evals/generation/work/tiered_trigger.jsonl \
  tests/evals/generation/work/tiered_resend.jsonl \
  --output tests/evals/datasets/real/pilot_v1.json

python tests/evals/generation/validate_rot_guard.py \
  tests/evals/datasets/real/pilot_v1.json \
  --db tests/evals/generation/work/pilot.db
```

Rot-guard exits 0 if all gold refs resolve and all anchors match.
Exit 1 lists the failures — those questions need to be dropped or regenerated.

---

## Step 7 — Commit the dataset

```bash
git add tests/evals/datasets/real/pilot_v1.json
git commit -m "data(evals): pilot gold dataset — mcp/trigger/resend, dual-model generation"
git push -u origin claude/local-first-context-review-fzlvng
```

---

## What to report back

After Step 6, paste:
1. The assembler's summary (question count + tier breakdown by pack)
2. The rot-guard result line

If any step fails, paste the last 20 lines of output from the failing command.

The committed `pilot_v1.json` is the artifact that unlocks the L1 retrieval runner
(ledger chunks e10+). Once it exists, the next session wires up
`pytest tests/evals/ --evals` against the real corpus.
