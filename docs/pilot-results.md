# Pilot Results — Scaled Eval Harness, L1 Retrieval

**Status**: Complete (2026-07-04). First real-corpus run of the gold-generation
pipeline (`docs/eval-harness-design.md`) and the L1 (model-free retrieval)
evaluation layer.

**Dataset**: `tests/evals/datasets/real/pilot_v1.json` — 114 questions
**Corpus**: mcp · trigger · resend, pinned `2025-05-29`, 6,181 indexed chunks
**Baseline**: `tests/evals/results/pilot_l1_baseline.json`

---

## What this pilot was for

The project keeps circling one question with no real evidence behind it:
**does FTS5 hit a wall on realistic user queries that a semantic/hybrid search
would fix, and how big is that wall?** The only data point before this pilot
was a 90-chunk hand-built toy corpus where NL paraphrase/vocab-mismatch queries
already scored `recall@10 = 0.000` — suggestive, but far too small to size an
embeddings investment or rule one out.

This pilot is the first test of that question at a real (if still small)
scale, and a dry run of the full generation pipeline before committing to it
across the entire ~100K-chunk corpus. See `docs/eval-harness-design.md` for the
full three-layer design (L1/L2/L3) and `docs/pilot-run-guide.md` for the
step-by-step operational log.

---

## How the dataset was built

Gold questions must read like real user input, not a model reverse-engineering
its own answer. Reading a chunk and writing "a question it answers" leaks the
chunk's vocabulary and only produces easy `direct` cases. The pipeline instead
decouples query from chunk text across four stages:

| Stage | What | Who |
|---|---|---|
| A | Chunk → neutral, vocabulary-stripped capability statement | Claude, in a Claude Code session |
| B | Capability → persona query pairs (never sees the chunk) | Claude (fresh session, expert + paraphrase) + local vLLM (Qwen3.6:27b, fp8, unquantized KV cache, reasoning off — vocab_mismatch + hurried) |
| C | Measure actual retrieval difficulty (Jaccard + live FTS5 rank), override the generator's self-labeled tier | Model-free |
| D | Assemble + rot-guard → dataset JSON | Model-free |

Two structural guarantees matter here:

- **Fresh-session isolation**: the Stage B strong half ran in a session that
  had only ever seen `(pack_name, chunk_id, capability)` records — never the
  chunk text, heading paths, or URLs — so gold vocabulary cannot leak into the
  queries it authored.
- **Measured, not claimed, difficulty**: `difficulty` in the final dataset is
  not the generator's self-label. Stage C recomputes it from lexical overlap
  and the gold chunk's actual FTS5 rank, so a "vocab-mismatch" question that
  turns out to be FTS5 rank-1 gets re-binned or dropped rather than inflating
  the hard tier.

**No Anthropic API key was used.** Both Claude halves (Stage A, Stage B strong)
were authored inside Claude Code sessions on this repo and merged by
model-free finalizers (`finalize_stage_a.py`, `finalize_stage_b.py`) that
validate prompt-rule conformance and join chunk metadata mechanically — the
sessions never transcribed a hash or URL. See decision D28.

### Pipeline output

| Stage output | Count |
|---|---|
| Chunks sampled for labeling | 240 (80 × 3 packs) |
| Raw persona queries (Stage B, both halves) | 960 (240 × 4 personas) |
| Kept after Stage C measured tiering | 128 |
| Dropped by Stage C (gold unreachable even by oracle keyword query, or mis-tiered) | 832 |
| Final, after Stage D dedup by (source_url, heading_path, difficulty) | 114 |

The large drop count is expected and correct, not a bug: Stage C's job is to
throw out unanswerable or mis-tiered questions rather than let them silently
inflate or deflate a score. `direct`/`paraphrase` survive tiering far more
often than `vocabulary_mismatch`, which is part of the finding below.

### Final composition (114 questions)

| | direct | paraphrase | vocabulary_mismatch | total |
|---|---:|---:|---:|---:|
| mcp | 10 | 25 | 0 | 35 |
| resend | 20 | 26 | 2 | 48 |
| trigger | 12 | 19 | 0 | 31 |
| **total** | **42** | **70** | **2** | **114** |

By model: 81 questions from `claude-session` (expert + paraphrase personas),
33 from the local vLLM model (vocab_mismatch + hurried personas).

The `vocabulary_mismatch` tier landing at n=2 is itself a finding, not a
generation failure — see below.

---

## L1 evaluation: what it measures

L1 is retrieval truth with **no model in the loop at eval time**. For each of
the 114 gold questions, `tests/evals/l1_retrieval.py` runs both query forms
through the same public API a real client calls (`synd.server.search_docs`)
against the indexed pilot corpus, and scores the ranked results against the
gold chunk using `tests/evals/metrics.py`:

- **`query`** — the natural-language form, as a human (or a model with weak
  vocabulary alignment) would actually type it.
- **`keyword_query`** — well-formed search terms, the "if you already knew the
  right words" case.

Gold chunks are matched to indexed chunk IDs by `content_hash` (the same
stable join key `stage_c_tier.py` uses), not by the pipeline's local
`chunk_id`, which is not a valid lookup key once multiple packs share one DB.

Because the dataset is frozen (Stage D) and L1 has no model in its own loop,
this number is not circular: the generator authored the *questions*, but the
retrieval score is a plain FTS5 query against a fixed index.

Reproduce with:

```bash
python tests/evals/l1_retrieval.py \
  tests/evals/datasets/real/pilot_v1.json \
  --db tests/evals/generation/work/pilot.db \
  --output tests/evals/results/pilot_l1_baseline.json
```

---

## Results

### Overall — the query-formulation tax

| | recall@1 | recall@5 | recall@10 | recall@20 | MRR | nDCG@10 |
|---|---:|---:|---:|---:|---:|---:|
| `keyword_query` | 0.367 | 0.723 | 0.777 | 0.841 | 0.536 | 0.594 |
| `query` (NL) | 0.110 | 0.167 | 0.184 | 0.184 | 0.135 | 0.146 |

The gap between these two rows is the cost of a user typing a real sentence
instead of well-chosen keywords — recall@20 drops from **0.841 to 0.184**, more
than 4x. FTS5 rewards exact-term matching; it does not bridge the gap to
natural phrasing on its own.

### By difficulty tier (NL query form)

| Tier | n | recall@1 | recall@5 | recall@10 | recall@20 | MRR | nDCG@10 |
|---|---:|---:|---:|---:|---:|---:|---:|
| direct | 42 | 0.298 | 0.452 | 0.452 | 0.452 | 0.359 | 0.382 |
| paraphrase | 70 | 0.000 | 0.000 | 0.029 | 0.029 | 0.004 | 0.009 |
| vocabulary_mismatch | 2 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |

This is the headline finding: **the 90-chunk toy-corpus result replicates at
real scale, with realistic distractors in the index.** Even the `direct` tier
— queries that share substantial vocabulary with the gold chunk or land in
FTS5's top 5 — only reaches recall@20 = 0.452 once it has to compete against
6,181 chunks instead of 90. `paraphrase` collapses to near-zero (0.029) and
`vocabulary_mismatch` to exactly zero. Under the keyword form, by contrast,
`paraphrase` still reaches 0.841 (see full baseline JSON) — confirming the gap
is specifically about *phrasing*, not about the underlying chunk being
unreachable.

**Caveat**: `vocabulary_mismatch` n=2 is too small to trust as a standalone
number — it corroborates the paraphrase-tier trend but cannot size it alone.
Stage C's tiering is working as designed (it does not let easy chunks
masquerade as hard ones), but it also means this pilot's 80-chunks-per-pack
sample produced very few genuinely vocabulary-divergent, still-reachable
questions. A full-scale run needs either a larger sample or persona prompts
tuned to produce more surviving hard-tier cases.

### By pack (NL query form)

| Pack | n | recall@1 | recall@5 | recall@10 | recall@20 | MRR | nDCG@10 |
|---|---:|---:|---:|---:|---:|---:|---:|
| mcp | 35 | 0.057 | 0.114 | 0.114 | 0.114 | 0.072 | 0.083 |
| resend | 48 | 0.156 | 0.229 | 0.250 | 0.250 | 0.191 | 0.205 |
| trigger | 31 | 0.097 | 0.129 | 0.161 | 0.161 | 0.117 | 0.128 |

All three packs score low on NL queries, with mcp weakest — plausibly because
MCP's docs are the most jargon-dense of the three (protocol/SDK terminology),
making the vocabulary gap between a user's question and the docs' wording
wider by default. Under the keyword form, mcp actually leads (recall@20 =
0.971) — the same effect in reverse: once the exact terms are given, MCP's
narrower, more distinctive vocabulary makes FTS5's job easier, not harder.

---

## Pipeline validation

Beyond the retrieval numbers, this pilot validated the mechanism itself:

- **Rot-guard passed clean**: all 114 gold entries' `(source_url,
  heading_path)` resolve, `content_hash` matches, and anchors are found in
  current chunk content — `ROT-GUARD OK: 114 gold entries verified`.
- **Zero gold-resolution failures** in the L1 run (`n_gold_unresolved: 0`) —
  every gold chunk's content_hash cleanly joined to a DB row.
- **In-session generation worked end to end** with no Anthropic API key and no
  transcription errors, since the finalizers — not the model — own all
  metadata joining.
- **Dual-model design is functioning as intended**: 81 Claude-authored
  questions read as well-formed expert/paraphrase queries; 33 local-model
  questions supply the terser, rougher vocab-mismatch/hurried style a
  small-model-operator population would actually produce.

---

## What this unlocks / next steps

1. **The sized case for embeddings** (feeds `docs/hybrid-search.md` / decision
   D25): NL paraphrase queries recovering only 2.9% of gold at k=20, at real
   corpus scale, is now real evidence rather than a 42-question toy result.
   Scaling the labeling subset (more packs, larger per-pack sample) would
   tighten the confidence interval and, specifically, grow the
   `vocabulary_mismatch` tier past n=2.
2. **L2 — agent retrieval competence**: run the search→fetch agent loop with a
   real served model over these same questions; `reachability_gap =
   L1_recall − L2_achieved_recall` isolates query-formulation skill from
   ceiling.
3. **L3 — end-task docs A/B lift**: the project's central question, gated by
   L2 — does having synd in the loop actually help a model complete a task,
   and does that lift scale inversely with model size as the VRAM-constrained
   thesis predicts.
4. **Baseline tracking**: commit `pilot_l1_baseline.json` as the first point in
   a `compare.py`-style delta report so this number stays live as the corpus
   and harness evolve (see `docs/pilot-run-guide.md` Step 8).

Full per-question scores are in `tests/evals/results/pilot_l1_baseline.json`;
the generation pipeline and step-by-step commands are in
`docs/pilot-run-guide.md`.
