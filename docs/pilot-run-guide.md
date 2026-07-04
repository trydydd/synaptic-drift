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
| A | Chunk → neutral capability statement | Claude, authored in a Claude Code session |
| B | Capability → persona query pairs | Claude (fresh Code session) + Qwen3-35B-A3B (vLLM) |
| C | Measure actual retrieval difficulty | Model-free (Jaccard + FTS5 rank) |
| D | Assemble + rot-guard → dataset JSON | Model-free |

No Anthropic API key is needed: the Claude halves of Stages A and B are
authored by Claude inside Claude Code sessions on this repo, then checked and
merged by model-free finalizer scripts (`finalize_stage_a.py`,
`finalize_stage_b.py`). The finalizers validate prompt-rule conformance and
join all chunk metadata mechanically, so the sessions never transcribe hashes
or URLs.

Work products go in `tests/evals/generation/work/` (gitignored). Run every
step — including both Claude Code sessions — on the same checkout, since the
sessions hand off through files in `work/`.

---

## Prerequisites

- `synd` CLI: `.venv/bin/synd` or `synd` in PATH (`pip install -e '.[all]'`)
- Claude Code, for the two authoring sessions (Stage A, Stage B strong half)
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

## Step 3 — Stage A: capability extraction (Claude Code session)

Open a Claude Code session on this repo and give it this task:

> Read `tests/evals/generation/prompts/stage_a.txt`. For each record in
> `tests/evals/generation/work/chunks_<pack>.jsonl`, apply the prompt's rules
> to the chunk's content and write one JSON line to
> `tests/evals/generation/work/session_a_<pack>.jsonl` with exactly these
> fields: `pack_name`, `chunk_id` (copied from the input record), and
> `capability` (the one-sentence statement you authored). Do this for all
> three packs (mcp, trigger, resend), then run the finalizer for each and fix
> any listed failures until it exits 0.

Then finalize (validates rules + coverage, joins metadata):

```bash
python tests/evals/generation/finalize_stage_a.py \
  --chunks  tests/evals/generation/work/chunks_mcp.jsonl \
  --session tests/evals/generation/work/session_a_mcp.jsonl \
  --output  tests/evals/generation/work/capabilities_mcp.jsonl

python tests/evals/generation/finalize_stage_a.py \
  --chunks  tests/evals/generation/work/chunks_trigger.jsonl \
  --session tests/evals/generation/work/session_a_trigger.jsonl \
  --output  tests/evals/generation/work/capabilities_trigger.jsonl

python tests/evals/generation/finalize_stage_a.py \
  --chunks  tests/evals/generation/work/chunks_resend.jsonl \
  --session tests/evals/generation/work/session_a_resend.jsonl \
  --output  tests/evals/generation/work/capabilities_resend.jsonl
```

---

## Step 4 — Stage B: query synthesis (fresh Claude session + 35B-A3B)

Two models per chunk: Claude generates expert + paraphrase queries; 35B
generates vocabulary-mismatch + hurried-user queries. The halves can run in
either order — both append into the same `raw_queries_<pack>.jsonl`.

### 4a — Strong half (Claude Code session)

**Isolation rule**: this must be a *fresh* session — not the Stage A session,
and not one that has read any pack, chunk file, or the pilot DB. The query
author must see only the capability statements; that is what keeps gold-chunk
vocabulary from leaking into the queries (`docs/eval-harness-design.md` §5).

Give the fresh session this task:

> Do not open any file under `tests/evals/generation/work/` except the
> `session_a_*.jsonl` files named here, and do not open any `.ctx` pack or
> database. Read `tests/evals/generation/prompts/stage_b_claude.txt`. For each
> record in `tests/evals/generation/work/session_a_<pack>.jsonl`, using ONLY
> the `capability` field, author the two persona query pairs the prompt
> describes and write two JSON lines to
> `tests/evals/generation/work/session_b_<pack>.jsonl`, one per persona, with
> exactly these fields: `pack_name`, `chunk_id` (copied), `persona`
> (`"expert"` or `"paraphrase"`), `nl_query`, `keyword_query`. Do this for all
> three packs, then run the finalizer for each and fix any listed failures
> until it exits 0.

Then finalize (validates personas + query shapes, joins metadata, merges):

```bash
python tests/evals/generation/finalize_stage_b.py \
  --capabilities tests/evals/generation/work/capabilities_mcp.jsonl \
  --session      tests/evals/generation/work/session_b_mcp.jsonl \
  --output       tests/evals/generation/work/raw_queries_mcp.jsonl

python tests/evals/generation/finalize_stage_b.py \
  --capabilities tests/evals/generation/work/capabilities_trigger.jsonl \
  --session      tests/evals/generation/work/session_b_trigger.jsonl \
  --output       tests/evals/generation/work/raw_queries_trigger.jsonl

python tests/evals/generation/finalize_stage_b.py \
  --capabilities tests/evals/generation/work/capabilities_resend.jsonl \
  --session      tests/evals/generation/work/session_b_resend.jsonl \
  --output       tests/evals/generation/work/raw_queries_resend.jsonl
```

### 4b — Weak half (vLLM)

```bash
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

The script always appends and skips chunks already present in the output, so
re-running after an interruption is safe.

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
