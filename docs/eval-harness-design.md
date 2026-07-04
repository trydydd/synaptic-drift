# Evaluation Harness Design — Retrieval Quality at Scale

**Status**: Design (2026-06-15). Supersedes the scope of the `chunk-e1..e9` eval
stream, which built the *mechanism* (metrics, loaders, runners, graders) against a
90-chunk toy corpus. This document scales that mechanism onto the real 100K-chunk
corpus with a defensible gold-label methodology.

## 1. Purpose

Produce an **evidence-backed, data-driven roadmap for query quality** and for
**synd's measured impact on models — especially VRAM-constrained ones**.

Two questions the project keeps circling, neither currently answerable because the
eval corpus is 90 hand-built chunks:

1. **Does FTS5 hit a wall that semantic/hybrid search would fix, and how big is it?**
   The only evidence today is 42 questions over a toy corpus, where NL
   paraphrase/vocab-mismatch queries already score `recall@10 = 0.000`. That is
   suggestive but far too small to size an embeddings investment or to rule it out.
2. **How much does retrieval help a given model, as a function of model size?**
   The thesis: small/quantized models have weaker parametric knowledge, so
   docs-in-context should help them *more* — but they are also worse at formulating
   queries that retrieve. These two effects must be measured separately or they
   cancel and tell us nothing.

This harness is **not** a corpus-construction project. The corpus already exists
(`scripts/build_benchmark_packs.py`). The gap is the gold-labeled evaluation set and
the layered metrics that turn it into roadmap evidence.

## 2. What already exists (build on, do not duplicate)

| Asset | Location | Reuse |
|---|---|---|
| 59-pack real corpus builder, llms-full.txt, pinned `2025-05-29`, ~100K chunks | `scripts/build_benchmark_packs.py` | Corpus source of truth |
| Retrieval metrics: `recall_at_k`, `mrr`, `ndcg_at_k` | `tests/evals/metrics.py` (chunk-e1) | L1 metrics |
| Dataset schema: `difficulty` ∈ {direct, paraphrase, vocabulary_mismatch}, dual `query`/`keyword_query`, `gold[]` (`source_path`+`heading_path`) | `tests/evals/datasets/*.json` | Dataset format, unchanged |
| Public retrieval API + query relaxation | `synd.server.search_docs`, `synd.search.fts.search_relaxed` | L1/L2 dispatch target |
| PR-delta comparison harness | `tests/benchmarks/compare.py` | Regression reporting pattern |
| Hybrid-search contingency design | `docs/hybrid-search.md`, decisions D25 | The thing L1 evidence informs |

The existing dataset schema is kept verbatim. We add scale, a generation pipeline,
and two measurement layers on top.

## 3. Three measurement layers

All three share **one** gold dataset and **one** pinned corpus.

### L1 — Retrieval truth (model-free at eval time)

The corpus-level ground truth. For each gold question, run the query through
`search_docs` and score the ranked chunk IDs against the gold set.

- Metrics: `recall@{1,5,10,20}`, `MRR`, `nDCG@10`.
- Two query forms per question: `keyword_query` (well-formed terms) vs `query`
  (natural-language, as a human types). The gap between them is the
  query-formulation tax.
- Sliced by `difficulty` tier and by pack/domain.
- **No model in the loop** — the LLM only authored the dataset, which is then
  frozen. L1 is therefore not circular.

L1 answers question (1): the FTS5 ceiling. If NL queries on the vocab-mismatch tier
score near zero at scale (confirming the toy-corpus 0.000), that is the quantified
case for embeddings, sized by how many real questions land in that tier.

### L2 — Agent retrieval competence (per served model)

Run the chunk-e8 agent loop (search → fetch, with relaxation, Alibaba sampling) with
a real served model. Measure how much of L1's *reachable* gold the model actually
retrieves through its own self-authored queries.

- `reachability_gap = L1_oracle_recall − L2_achieved_recall`, per tier, per model.
- Run across a **model-size sweep**: Qwen3 {0.6B, 4B, 8B, 14B, 30B-A3B} + the
  operator's vLLM target. (The 0.6B/4B/8B/14B GGUFs are already validated in-sandbox;
  30B-A3B is the user's weak-generator model and doubles as an L2 subject.)

L2 isolates "can this model phrase a retrieving query" from "is the answer reachable
at all" (L1). This is the query-formulation-competence-by-size curve.

### L3 — End-task docs A/B lift (per served model)

The project's central question. Each end-task runs two arms — `no_docs` (task prompt
only) and `with_docs` (search/fetch tools) — graded by chunk-e7. Run across the same
model-size sweep.

- Headline deliverable: **lift = with_docs_pass − no_docs_pass, plotted over model
  size.** The VRAM-constrained thesis predicts the lift curve is *higher* for smaller
  models (weaker priors → more to gain) but is *gated* by L2 (if they can't retrieve,
  they can't benefit).

## 4. The corpus (pinned snapshots + rot-guard)

- Source: the 59 packs already enumerated in `build_benchmark_packs.py`, pinned at
  `VERSION = "2025-05-29"`. Built `.ctx` packs are the committed/cached fixtures.
- **Labeling subset**: not all 59 packs need gold questions. Select ~15–20 packs
  spanning domains (payments, infra, AI/ML, devtools, data) to avoid domain
  overfitting. The full 100K corpus is still loaded as the *index* so every question
  competes against realistic distractors — a key difference from the 90-chunk toy
  corpus where the right answer had almost no competition.
- **Determinism**: LLM generation is a one-time construction step (like building the
  packs). The committed dataset JSON is the artifact; eval runs replay it and are
  bit-reproducible. Generation is never part of an eval run.
- **Rot-guard**: each gold entry stores `content_hash` + a short anchor substring. On
  corpus rebuild, a validator checks the anchor still resolves to the same chunk;
  drift raises `EvalDatasetError` (the existing pattern in `live_mcp.json`) and flags
  the question for regeneration. This is how "pinned snapshot" survives upstream doc
  changes without silently corrupting gold.

## 5. Gold-label generation (the crux)

Requirement from the design decision: queries must read like **human user input, not
model reverse-engineering**. The naive "read this chunk, write a question it answers"
approach leaks the chunk's vocabulary and manufactures only easy `direct` cases. We
decouple the query from the chunk text in four stages.

**Stage A — Capability extraction (Claude, in a Claude Code session).**
Read a chunk → emit a neutral, vocabulary-stripped statement of the *user-facing
intent* it serves (e.g. "how to stream incremental progress from a long-running tool
to the client"). This is the bridge; the chunk's exact wording is dropped here.
Authored in-session rather than via API calls (D28); `finalize_stage_a.py`
validates rule conformance and joins chunk metadata mechanically.

**Stage B — Persona query synthesis (Claude in a *fresh* Code session *and*
Qwen3-30B-A3B, independently).**
Given **only the capability statement** (never the chunk text) plus a user persona,
produce the query a real user would type. The fresh-session requirement enforces
the decoupling structurally: the authoring session's only inputs are
`(pack_name, chunk_id, capability)` records, so gold vocabulary cannot leak
through context carried over from Stage A. Personas span the realistic query-quality
spectrum:
- Expert who knows the exact term → tends toward `direct`.
- Developer who knows the concept but not this library's vocabulary → `paraphrase`.
- Developer arriving from a different ecosystem using foreign terms → `vocabulary_mismatch`.
- Hurried user typing fragments / lowercase / partial → realistic noise.

The **strong+weak model mix is itself the instrument**: Claude produces well-formed
and deliberately-varied queries; Qwen3-30B-A3B's terser, rougher output mirrors the
hurried-user and the small-model-operator population. Two model families also reduce
single-model stylistic bias.

**Stage C — Measured tiering & validation (model-free).**
Difficulty is **measured, not claimed**:
- Compute lexical overlap (Jaccard over content terms) between query and gold chunk,
  and the gold chunk's actual FTS5 rank under the keyword form.
- Assign `difficulty` from measured retrieval distance, overriding the generator's
  self-label. A "vocab-mismatch" question whose gold is FTS5 rank-1 is mis-tiered and
  gets re-binned or dropped.
- Drop questions where gold is unreachable even by an oracle keyword query
  (un-answerable / bad generation).
- Hold out a **human-audited sample (~100–150 questions)** to calibrate generator
  quality and the tier thresholds, and to bound the false-gold rate.

**Stage D — Freeze & commit.**
The validated dataset is pinned to the corpus version and committed. Rot-guard
governs refresh.

This directly satisfies "human-like, not reverse-engineered": queries are synthesized
from a decoupled capability statement under user personas, and vocabulary divergence
is *verified by measurement* rather than assumed.

### Circularity & leakage defenses (summary)
- L1 (the FTS5-vs-semantic evidence) has no model at eval time — the dataset is frozen.
- Query vocabulary is decoupled from gold text (Stage A→B) and divergence is measured (Stage C).
- Two independent model families author queries; a human-audited sample bounds error.
- Tiers are measured, so a generator that fails to produce hard cases shows up as an
  empty hard tier, not as inflated scores.

## 6. Roadmap output (what the harness produces)

The deliverable is not a pass/fail gate but a **decision table**:

1. **FTS5 ceiling by tier** (L1): recall on each difficulty tier, keyword vs NL. The
   vocab-mismatch NL number, multiplied by the tier's share of realistic questions,
   is the sized case for/against embeddings — feeding `docs/hybrid-search.md` / D25.
2. **Reachability gap by model size** (L1−L2): how much retrievable answer each model
   leaves on the table through poor query formulation. Large gaps argue for query
   assistance (relaxation tuning, synonyms, query rewriting) *before* embeddings, and
   quantify the win.
3. **Docs-lift curve by model size** (L3): the headline for "is synd worth it for
   small models," with L2 as the gating explanation.

Reported in the `compare.py` style (committed baselines + PR delta) so the roadmap
stays live across releases.

## 7. Proposed ledger decomposition (chunks e10+)

- **e10 — corpus fixture & rot-guard**: pin/build the labeling-subset packs; rot-guard
  validator (`content_hash`+anchor); full-corpus index loader for distractor realism.
- **e11 — gold generation pipeline**: Stages A–D under `tests/evals/generation/`;
  Claude halves authored in Claude Code sessions with model-free finalizers
  (D28); Qwen3-30B-A3B via the same OpenAI-compatible client as e6; measured
  tiering; outputs versioned JSON.
- **e12 — human-audit tooling**: sampling + review CLI; false-gold-rate report.
- **e13 — L1 runner at scale**: extend the e4/e5 retrieval runner to the new dataset;
  per-tier/per-pack slicing; baseline JSON.
- **e14 — L2 agent-competence runner**: model-size sweep over the e8 loop; reachability
  gap metric.
- **e15 — L3 A/B sweep & report**: lift-by-size; `compare.py`-style roadmap artifact.

## 8. Risks

- **Generation cost**: ~1–2k questions × multi-stage × two models. Bounded by sampling
  packs and caching Stage A. One-time, not per-run.
- **False gold**: mitigated by measured tiering + human-audited sample; report the
  bound rather than claiming zero.
- **Weak-model judge trap**: Qwen3-30B-A3B is a query *author*, never a grader. All
  grading is model-free (L1) or regex/execution (L3, chunk-e7).
- **Domain skew**: fixed by spanning packs across domains and reporting per-pack.
