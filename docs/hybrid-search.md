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
  ~80–400MB model download depending on the chosen model. Lightweight by embedding
  library standards.
- A small ONNX model such as `all-MiniLM-L6-v2` (384 dimensions, ~90MB).

Ruled out:
- **`sentence-transformers`** — pulls in PyTorch (~2GB). Too heavy for a CLI tool.
- **API embeddings** — violates the "no outbound network calls at query time" constraint.
  Also couples build correctness to an external service.

### Consumer (query time)

- **`sqlite-vec`** — SQLite extension for vector column type and ANN search. Small
  shared library, no model weights. Keeps the vector index inside the existing
  `index.db` with no separate service required.

## File size

At 384 dimensions (float32), each chunk's embedding is ~1.5KB.

| Corpus size | Embedding overhead |
|---|---|
| 100 chunks | ~150KB |
| 1,000 chunks | ~1.5MB |
| 10,000 chunks | ~15MB |

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
