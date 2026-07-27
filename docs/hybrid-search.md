# Hybrid Search

## Why

Synaptic Drift's current FTS5/BM25 search is lexical: a query term contributes
nothing unless the same token (no stemming, no synonyms) appears in the chunk.
An LLM agent bridges vocabulary gaps by reformulating queries before calling
`search` — but that compensation is small and cannot be relied on (measured
below).

**The failure shape (corrected 2026-07-11).** This document originally framed
the problem as 0-result failures ("returns nothing → agent falls back to
WebFetch at full page cost"). That framing predates the OR-semantics search
(`search_relaxed`, D-era of the v0.3.0 branch): with terms OR-joined and
BM25-ranked, a query almost never returns *zero* results. The measured failure
is instead a **ranking-precision failure inside a non-empty result set** — the
gold chunk is absent from the top-k while plausible-looking wrong chunks fill
it. The cost is not a WebFetch fallback but worse: the agent confidently
fetches and uses the wrong documentation (observed directly in the L2 run —
e.g. html_v1 r0088, where the model fetched the `text.usetex` page when the
gold answer was the adjacent Mathtext page).

**This is a reliability improvement, not a token efficiency improvement.**
When FTS5 ranks the right chunk into the top-k, an agent reading summaries and
selecting the right one already achieves ~84% token savings vs WebFetch.
The value of hybrid search is turning buried/missing gold chunks into
top-k hits on vocabulary-mismatched queries.

## Evidence (html_v1 + pilot_v1 gold corpora, 2026-07)

The preconditions in "Deferred until" (below) have been evaluated against real
measurements — see `tests/evals/results/html_l1_baseline.json` and
`html_l2_reachability.json`, and decisions.md D25/D29/D30:

- **L1 (engine ceiling, no model)**: `vocabulary_mismatch` tier recall@1–20 =
  **0.000 at n=26**; `paraphrase` recall@5 = 0.000 at n=98. Reproduced across
  two independently generated corpora (llms.txt pilot and crawled HTML).
- **L2 (Qwen3.6-27B authoring its own queries via search/fetch tools)**:
  recovers only recall@5 = 0.082 (vocab-mismatch) / 0.143 (paraphrase). The
  model does retry (avg 2.2 searches/question) but retries fail the same
  lexical way. A 27B model is the *largest* in the intended sweep — smaller
  target models are expected to compensate less, not more.
- **Noise audit of the vocab-mismatch tier** (all 26 NL queries checked
  against the full corpus vocabulary): zero generation artifacts; the tier is
  measuring what it claims. Composition, with the fix each class actually
  needs:

  | class | n | mean L2 recall@5 | fixable by |
  |---|---|---|---|
  | pure word-choice (all tokens exist in corpus, wrong ones for the gold chunk) | 14 | 0.071 | semantic matching — embeddings' core case |
  | cross-ecosystem analogy (rails/gem/jdbc/puma, persona by design) | 6 | 0.167 | embeddings *maybe* — conceptual analogy is harder than synonymy for a small ONNX model |
  | morphology (formulas/formula, savable — unicode61 does no stemming) | 4 | 0.000 | **porter stemmer**: zero-dependency FTS5 tokenizer change |
  | typo/abbreviation (surfce, perms) | 2 | 0.060 | fuzzy matching / spellfix — no embeddings needed |

The decomposition sets expectations honestly: hybrid search should recover
much of the word-choice majority (~54% of the tier), some of the
cross-ecosystem class, and is the wrong tool for the ~23% that cheap lexical
fixes address. Sequencing decision recorded in decisions.md **D30** (stemmer
first, then embeddings sized against the post-stemmer re-measure).

## Architecture

The design constraint is: **no burden to the user beyond `.ctx` file size and a small
query-time SQLite extension.**

This is achieved by moving all model-related work to build time:

1. **Build time** (`synd build`) — the pack publisher generates embeddings for every
   chunk using a local ONNX model. Embeddings are stored inside the `.ctx` archive
   alongside `chunks.jsonl`. The publisher bears the model dependency and the
   (potentially slow) generation cost. This is a one-time cost per pack version.

2. **Pack** (`.ctx`) — the archive bundles pre-computed vectors. No model is required
   to consume the pack. File size increases proportionally to corpus size (see below).

3. **Add time** (`synd add`) — vectors are extracted from the `.ctx` and loaded into
   a `sqlite-vec` virtual table in `index.db`, alongside the existing FTS5 index.
   No model, no generation.

4. **Query time** — `search` runs both FTS5 and a vector ANN search, combines
   scores via Reciprocal Rank Fusion (RRF), and returns the merged ranked list.
   The only runtime dependency is the `sqlite-vec` SQLite extension.

## Dependencies

### Publisher (build time)

- **`fastembed`** — ONNX-based embedding library, no PyTorch. ~50MB runtime plus
  ~80MB–1.2GB model download depending on the chosen model. Lightweight by embedding
  library standards.
- **`BAAI/bge-large-en-v1.5`** (1024 dimensions, ~1.2GB ONNX, ~30ms/query on CPU) —
  recommended encoder as of S15 (decisions.md D33). `BAAI/bge-base-en-v1.5`
  (768 dimensions, ~0.21GB quantized ONNX, ~10ms/query) is the lighter fallback
  when the extra ~1GB matters more than the last few points of recall.
  `all-MiniLM-L6-v2` (384 dimensions, ~90MB) was the original D30 prototype
  candidate; bge-large/bge-base measurably beat it on both gold corpora at a
  still-modest size (D33) and are the current recommendation.

Ruled out:
- **`sentence-transformers`** — pulls in PyTorch (~2GB). Too heavy for a CLI tool.
- **API embeddings** — violates the "no outbound network calls at query time" constraint.
  Also couples build correctness to an external service.
- **`BAAI/bge-m3`** (dense + learned-sparse + ColBERT) — evaluated in S15 and
  rejected. No PyTorch-free path exists (reference implementation requires
  `torch` + `FlagEmbedding`; a full eval venv measured at 1.4GB with a further
  4.3GB of model weights, against bge-large's PyTorch-free 1.2GB). Its dense
  leg's quality lift is real but is fully captured by the PyTorch-free
  bge-large encoder above; its learned-sparse leg — the reason BGE-M3 was
  considered in the first place — actively underperforms plain BM25+MiniLM
  fusion on the vocabulary-mismatch tier it was meant to fix, while taxing the
  direct tier hard (html direct recall@5 0.972 → 0.804 standalone). See D33.

### Consumer (query time)

- **`sqlite-vec`** — SQLite extension for vector column type and ANN search. Small
  shared library, no model weights. Keeps the vector index inside the existing
  `index.db` with no separate service required.

## File size

At bge-large's 1024 dimensions (float32), each chunk's embedding is ~4KB
(bge-base at 768 dimensions: ~3KB; the original MiniLM prototype at 384
dimensions: ~1.5KB).

| Corpus size | bge-large overhead | bge-base overhead |
|---|---|---|
| 100 chunks | ~400KB | ~300KB |
| 1,000 chunks | ~4MB | ~3MB |
| 10,000 chunks | ~40MB | ~30MB |

Whether this is acceptable depends on how packs are distributed. For local builds where
the publisher and consumer are the same person, it is a non-issue. For a future registry
where packs are downloaded, the size increase should be documented alongside the pack.

## Score fusion

BM25 and cosine similarity scores are on different scales and cannot be added directly.
Reciprocal Rank Fusion (RRF) is the standard approach: each result is scored as
`1 / (k + rank)` from each retriever independently, then the scores are summed. This
requires no tuning and is robust to score scale differences.

## Deferred until — status

Hybrid search was explicitly deferred past MVP. CLAUDE.md states:
`"SQLite FTS5 is the only search backend for MVP. No embedding dependencies."`

Preconditions for revisiting, and their status as of 2026-07-11:

- ✅ **The `search-docs` / `fetch-docs` endpoint split (D12)** shipped in
  v0.2.0 — it was the higher-priority search improvement and required no new
  dependencies.
- ✅ (**restated**) **Evidence from a real multi-document corpus.** The
  original wording asked for evidence of frequent *0-result* failures; the
  measured failure shape is ranking-precision misses in non-empty results
  (see "Why" above), which carries a worse cost (wrong docs used
  confidently, not a visible fallback). Both gold corpora provide the
  evidence at trustworthy sample sizes — see "Evidence" above.

Sequencing before implementation is recorded in decisions.md **D30**: the
porter-stemmer tokenizer change ships and is re-measured first (it is
near-free and addresses the tier's morphology class plus general recall),
then the embedding investment is sized against what remains.

## Prototype results and the query-embedding gap (2026-07-12)

A working RRF prototype exists (`tests/evals/l1_rrf_matrix.py`: BM25 via the
shipped `search_docs` path + brute-force cosine over precomputed
all-MiniLM-L6-v2 chunk embeddings, RRF k=60, fusion depth 100). Measured
across {unicode61, porter} × {BM25, RRF} on both gold corpora — full numbers
in `tests/evals/results/*_l1_rrf_matrix.json` and decisions.md D30's matrix
addendum. Headlines: RRF beats BM25-only overall on both corpora and takes
the paraphrase tier from 0.000 to ~0.39 recall@5, but pays a direct-tier
recall@5 tax (html 0.955 → ~0.85); with RRF active, porter-vs-unicode61
becomes nearly irrelevant.

**Query-embedding gap (design correction).** The architecture above moves
chunk embedding to build time, but a query arriving at search time still
needs an encoder before the vector leg can run. There is no model-free way
to produce the query vector. The resolution is a hard product requirement
(D30): **the model-free workflow is preserved** — the vector leg activates
only when (a) the pack shipped embeddings and (b) a local encoder is
available at query time; in every other case `search` runs today's
BM25-only path unchanged. The encoder is an optional extra (e.g.
`synaptic-drift[semantic]` pulling fastembed), never a base dependency, and
never a network call.

## Context7 head-to-head, vocab-mismatch tier (2026-07-14)

All 26 html_v1 `vocabulary_mismatch` NL queries were sent verbatim to
Context7's live `query-docs` (website-scraped library variants matching our
crawled corpora: `/websites/matplotlib_stable`, `/websites/sqlalchemy_en_20`,
`/websites/fastapi_tiangolo`) and to synd's enriched-append artifacts.
Scoring is **page-level** for both sides (their chunking differs; gold page =
the gold chunk's `source_url`, with same-content path normalization, e.g.
`_downloads/*.ipynb` ↔ its gallery page). synd ranks come from deduped
page order of the ranked chunk list.

| system | page-recall@1 | page-recall@5 |
|---|---|---|
| Context7 (enrich + hybrid + reranker, server-side) | **10/26** | 16/26 |
| synd hybrid prototype (enriched-append, unweighted RRF) | 6/26 | **17/26** |
| synd shipping path (enriched-append, BM25-only) | 5/26 | 12/26 |

Readings, with the caveat that n=26 and this is one manual-judged run:

- **At k=5 the hybrid prototype matches Context7** (17 vs 16, different
  misses). The "0.038 recall@5" chunk-level number that looks catastrophic
  is a strict-single-chunk artifact; at the granularity an agent actually
  works at (land on the right page, then read), synd's step-2 architecture
  is already at parity on the adversarial tier.
- **Context7 keeps a clear rank-1 edge** (10 vs 6). That is their query-time
  reranker earning its keep — the one architectural layer synd deliberately
  does not have (no model at query time). Under D12's workflow the agent
  reads top-k summaries rather than trusting rank 1, which is exactly the
  mitigation for lacking a reranker.
- **The BM25-only shipping path trails but is serviceable** (12/26 vs 16/26
  on the tier engineered to defeat lexical search) — and it needs zero
  query-time dependencies, works offline, and is what enrichment alone buys.
- Context7's index has its own enrichment noise (one snippet attributes
  arrow-annotation content to matplotlib's `dfrac_demo` fractions page), and
  several of its strict misses were functionally-correct alternative pages —
  single-gold strictness cuts against both systems equally.
