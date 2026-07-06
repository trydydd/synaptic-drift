# Scaled Eval Harness — Pilot Run Guide

**Branch**: `claude/local-first-context-review-fzlvng`
**Corpus**: mcp · trigger · resend (3 pilot packs, ~100K-chunk index)
**Output**: `tests/evals/datasets/real/pilot_v1.json` (commit this when done)

---

## Why this exists

Synaptic Drift is a local search index for library/API documentation: it lets a
coding agent (or a small, VRAM-constrained local model that doesn't have much
memorized knowledge) look up real docs instead of guessing from what it learned
in training. Whether that's actually *useful* depends entirely on one thing:
can the search actually find the right doc page when someone asks in the way
real developers ask — vague, paraphrased, or using the wrong vocabulary — not
just when they type the exact words from the docs?

Right now that question is answered by a 90-chunk toy dataset that was hand-built
for convenience, not realism. It's too small to say anything with confidence, and
on the hardest query types it already shows scores near zero — which could mean
"the search engine has a real ceiling" or could just mean "42 questions isn't
enough data to trust." Nobody can currently tell which.

This pilot run is the first real-world-scale test of that question, plus a dry
run of the pipeline that will produce it at full scale. It builds a small
(3-library, ~240-chunk) but methodologically honest gold-label dataset — one
where the "queries" are authored from an abstract description of what a chunk is
useful for, never from the chunk's own wording, so the exercise doesn't
accidentally test whether the query repeats the answer. Getting this pilot right
is what justifies (or rules out) investing in more advanced search (e.g. semantic
embeddings) before doing so at the full ~100K-chunk scale, and it's a cheap,
fast way to catch pipeline bugs before that much bigger run.

---

## Context

This is the generation pipeline for the scaled retrieval-quality eval harness
described in `docs/eval-harness-design.md`. The skeleton is committed; this
guide runs it end-to-end on your machine.

The pipeline has four stages:

| Stage | What | Model |
|---|---|---|
| A | Chunk → neutral capability statement | Claude, authored in a Claude Code session |
| B | Capability → persona query pairs | Claude (fresh Code session) + local vLLM (Qwen3.6:27b fp8 + unquantized KV cache, no reasoning in pilot) |
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
- vLLM serving a model locally — for the weak half of Stage B (pilot used Qwen3.6:27b with fp8 quantization, unquantized KV cache, reasoning off)
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

## Step 4 — Stage B: query synthesis (fresh Claude session + vLLM)

Two models per chunk: Claude generates expert + paraphrase queries; vLLM
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

## Step 8 — L1 Evaluation: Model-Free Retrieval Quality

The gold dataset unlocks **L1 evaluation** — measuring FTS5 retrieval performance
without a model in the loop. This is the evidence for: "Does FTS5 hit a wall that
embeddings/hybrid search would fix?"

`tests/evals/l1_retrieval.py` runs both query forms (`query` = natural language,
`keyword_query` = well-formed terms) from every gold question through the public
`synd.server.search_docs` API against the indexed pilot DB, matches ranked chunk
IDs to gold via `content_hash` (the same stable join key `stage_c_tier.py` uses),
and scores with `tests/evals/metrics.py` (`recall_at_k`, `reciprocal_rank`,
`ndcg_at_k`):

```bash
python tests/evals/l1_retrieval.py \
  tests/evals/datasets/real/pilot_v1.json \
  --db tests/evals/generation/work/pilot.db \
  --output tests/evals/results/pilot_l1_baseline.json
```

Prints a summary (overall by query form, sliced by difficulty tier and pack) and
writes the full per-question breakdown to `--output`. Exit 0 on a completed run
— this is a measurement, not a pass/fail gate — non-zero only if the dataset/DB
can't be read or every question's gold is unresolvable (a stale/mismatched DB).

**Pilot result** (114 questions, mcp/trigger/resend, `pilot.db`):

| | recall@1 | recall@5 | recall@10 | recall@20 | MRR | nDCG@10 |
|---|---:|---:|---:|---:|---:|---:|
| `keyword_query` | 0.367 | 0.723 | 0.777 | 0.841 | 0.536 | 0.594 |
| `query` (NL) | 0.110 | 0.167 | 0.184 | 0.184 | 0.135 | 0.146 |

By tier (NL form): `direct` recall@20 = 0.452, `paraphrase` = 0.029,
`vocabulary_mismatch` = 0.000 (n=2, too small to trust on its own). This
reproduces the toy-corpus finding — NL paraphrase/vocab-mismatch queries score
near zero — at real scale, with realistic distractors in the index. That is the
sized evidence for `docs/hybrid-search.md` / decision D25.

**Next steps** (ledger chunks e13+):
- Scale the labeling subset past 3 packs to tighten the `vocabulary_mismatch`
  tier (n=2 here is not enough to size an embeddings investment on its own)
- Commit the baseline JSON and wire a `compare.py`-style delta report so the
  roadmap number stays live across corpus/harness changes
- Feed this baseline into L2 (agent retrieval competence) and L3 (end-task lift)

---

## Step 9 — L2/L3: end-task A/B harness

L1 (Step 8) measures retrieval with no model in the loop. The next question —
does a *served model* actually benefit from that retrieval — needs an agent
loop and a real coding-task comparison. That harness (`tests/evals/endtask.py`)
was built 2026-07-05; see `docs/pilot-results.md` §"L2/L3: end-task A/B
harness" for what it measures, what it's built on (chunk-e1/e2/e3/e6/e7/e8),
and the caveat about the system prompt's search-semantics wording being
unverified against a live model until you run this.

It targets a different, smaller corpus than Steps 1–8: the hermetic
`evalcorpus` fixture (`tests/evals/fixtures/corpus/`, 19 markdown files) —
not the mcp/trigger/resend pilot corpus. No setup needed; the fixture builds
itself on first use.

**Prerequisites**: a vLLM (or any OpenAI-compatible server) endpoint reachable
from this machine, with tool calling enabled.

```bash
# 1. Confirm the endpoint is up and get the exact served model name —
#    SYND_EVAL_MODEL must match a name from this list exactly.
curl -s http://192.168.0.214:8000/v1/models | python3 -m json.tool

# 2. Tool calling requires vLLM to have been launched with these flags
#    (restart vLLM if it wasn't):
#      --enable-auto-tool-choice --tool-call-parser hermes   # hermes for Qwen models

# 3. Run the live end-task eval — 10 tasks x 2 arms x 1 rep = 20 model turns
#    minimum (more if with_docs needs multiple search/fetch round trips).
export SYND_EVAL_BASE_URL=http://192.168.0.214:8000/v1
export SYND_EVAL_MODEL=<exact-name-from-step-1>
# export SYND_EVAL_API_KEY=...   # only if vLLM requires auth

.venv/bin/pytest tests/evals/test_endtask.py --evals --live-model -s \
  -k test_endtask_eval_live
```

Writes `tests/evals/results/endtask_latest.json`:

```json
{
  "meta": {"timestamp": "...", "git_commit": "...", "model": "...",
            "reps": 1, "max_turns": 8, "task_count": 10},
  "arms": {"no_docs": {"pass_rate": 0.0, "n": 10},
           "with_docs": {"pass_rate": 0.0, "n": 10}},
  "per_task": [ {"task_id": "t01", "arm": "no_docs", "rep": 0, "passed": false,
                 "failures": [...], "turns_used": 1, "tool_calls_made": 0,
                 "reply_chars": 0, "error": null}, ... ]
}
```

`arms.with_docs.pass_rate − arms.no_docs.pass_rate` is the headline number.
`per_task[].error == "max_turns"` flags a task where the model looped tool
calls without converging (8-turn budget by default); `per_task[].failures`
names which specific `must_match`/`must_not_match`/`must_parse` criterion
failed, for reading *why* a task failed, not just that it did.

Without `--live-model` (or without both env vars set), the live test skips
cleanly with the exact command above printed as the skip reason — safe to
leave in the default `--evals` run. The other 9 tests in
`tests/evals/test_endtask.py` use a scripted `FakeChatClient` and need no
endpoint at all.

**Next step once a run exists**: repeat across the model-size sweep the
design doc calls for (Qwen3 0.6B / 4B / 8B / 14B / 30B-A3B, or whatever the
operator's vLLM serves) to get the `reachability_gap` (L1 − L2) and
docs-lift-by-size curves — the project's actual headline deliverable. Commit
each `endtask_latest.json` under a model-specific name (e.g.
`endtask_qwen3-8b.json`) before the next run overwrites it.

### Reasoning mode is a confound, not just a knob

`ChatClient` defaults to whatever the endpoint's default reasoning behavior is.
For a reasoning model (Qwen3-family and similar), that matters more than it
looks: the `no_docs` arm's completions can run 5-10x longer than `with_docs`
(no retrieved context to converge on, more open-ended reasoning), which both
inflates wall-clock cost and — on a pilot run — pushed `no_docs` past a
too-short client timeout far more often than `with_docs`, making the
"docs lift" mostly an artifact of which arm timed out rather than which arm
answered correctly. Two things fix this:
- `ChatClient(..., timeout=1800.0)` (the current default) so slow reasoning
  completions aren't mistaken for failures.
- `SYND_EVAL_DISABLE_THINKING=1` (or `ChatClient(..., disable_thinking=True)`)
  to send `chat_template_kwargs: {"enable_thinking": false}` and get fast,
  non-reasoning completions instead — changes what's being measured (a
  model's non-reasoning ability, not its default mode), so treat thinking-on
  and thinking-off runs as separate conditions, not interchangeable samples.

### Repeat runs for variance (`scripts/run_endtask_repeats.py`)

A single live run is one nondeterministic sample (model sampling, network
jitter). To get a mean/stdev instead of a single point estimate:

```bash
export SYND_EVAL_BASE_URL=http://<host>:8000/v1
export SYND_EVAL_MODEL=<served-model-name>
python scripts/run_endtask_repeats.py --runs 10
```

Builds the eval corpus and model client once, then calls `run_endtask_eval()`
directly `--runs` times (no pytest/subprocess overhead per repeat) — always
with reasoning disabled, since that's the fast/comparable-latency condition.
Prints a header (endpoint, model id + root name + max_model_len fetched live
from `GET /models`, sampling params, timeout) before the run, then each
repeat's pass_rate/avg_latency_s as it completes. Writes:

- `tests/evals/results/endtask_repeats/<model>/run_XX_<timestamp>.json` — one
  full payload per repeat
- `tests/evals/results/endtask_repeats/<model>/summary_<timestamp>.json` —
  per-arm mean/stdev of `pass_rate` and `avg_latency_s` across all repeats,
  plus the header, plus a per-arm `convergence` block: `answered_rate`
  (fraction of task-runs that produced a graded answer at all),
  `correct_given_answered` (of those, how many passed — separates "wrong
  code" from "never converged"), and `correct_at_turns` (cumulative
  correct@k by turns used; in the with_docs arm 3 turns = 1 search round,
  so `correct_at_turns["3"]` reads as "right answer from a single search")

Output is model-scoped and timestamped, so successive batches (different
models, different configs) never overwrite each other. First live results
from this harness are written up in `docs/pilot-results.md` §"First live
results — Qwen3.6-27B-FP8".

---

## What to report back

After Step 7, paste:
1. The assembler's summary (question count + tier breakdown by pack)
2. The rot-guard result line

After Step 8, paste the L1 summary printed to stdout.

After Step 9, paste the `arms` block from `endtask_latest.json` (or the
skip message, if no endpoint was available).

If any step fails, paste the last 20 lines of output from the failing command.

The committed `pilot_v1.json` is the gold dataset; `l1_retrieval.py` turns it into
the FTS5-ceiling evidence per difficulty tier, informing the case for/against
embeddings investment.
