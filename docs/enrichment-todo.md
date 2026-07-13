# LLM Summary Enrichment — Prototype → Production TODO

Tracks the remaining work between the D30 enrichment prototype
(`tests/evals/generation/enrich_summaries.py` + `build_enriched_artifacts.py`,
eval-side scripts) and a shipping `synd build --summarizer llm` (the upgrade
path D3's revisit clause reserved). Check items off as they land; move settled
design questions into `decisions.md` entries and link them here.

Context: the html_v1 matrix (`tests/evals/results/html_l1_rrf_matrix_enriched.json`,
D30 addendum) showed enrichment is the first strict improvement on the D30
ladder — direct tier preserved-or-better under every strategy, paraphrase and
vocab-mismatch up on every strategy, as a pure build-time change with zero
query-time dependencies.

**Out of scope for this doc**: weighted RRF and sqlite-vec (D30 step 2 — ships
after enrichment, tracked in `hybrid-search.md`), and the model-size sweep.
Nothing below depends on the vector leg.

## 1. Measurements to complete

- [x] **Replicate on the pilot corpus** *(done 2026-07-13, see D30 pilot
  addendum + `pilot_l1_rrf_matrix_enriched.json`)*. Headline replicates:
  every strategy improves on every overall NL metric; paraphrase up across
  the board. Caveat: BM25-only direct recall@5/@20 dipped on 5 terse
  abbreviated queries (washes out under rrf-w3) — spawned the append-vs-
  replace variant below.
- [x] **Measure append-vs-replace summaries** *(done 2026-07-13, see D30
  addendum + `*_l1_rrf_matrix_enriched_append.json`)*. **Append is the
  production summary format**: BM25-only append ≥ replace on both corpora
  (html direct recall@5 0.972 vs 0.955; repairs the pilot terse-query
  regression) while keeping the hard-tier gains. The embedding leg prefers
  the clean LLM sentence (html vector-only 0.818 vs 0.744) — when D30
  step 2 ships, embed the LLM sentence, index/display the append form; the
  choices are independent.
- [ ] **One L2 confirmation run against the enriched html DB.** L1 says the
  engine ceiling rose; the product claim is that the *agent* benefits. A
  single `l2_reachability.py` pass on the enriched artifacts closes the loop
  and shows whether the recall@1 lift (0.473 → 0.568 BM25-only) reduces
  wrong-fetch behavior of the kind observed in the L2 baseline (e.g. html_v1
  r0088).
- [ ] **Semantic spot-check of generated summaries.** The 6,469-summary html
  run had zero API failures, but nobody has audited whether summaries
  *accurately describe their chunks*. Sample ~30 across the three packs and
  check for misdescription, hallucinated capabilities, and refusal/preamble
  leakage. An LLM summary that misdescribes its chunk is indexed at 1.5×
  BM25 weight — errors here actively mislead retrieval.

## 2. Design decisions to make

Each of these needs a `decisions.md` entry when settled.

- [ ] **D5 reproducibility contract (the blocker — settle first).** Summaries
  are bytes in `chunks.jsonl`, so they are inside `pack_digest`. Greedy
  decoding is necessary but not sufficient: vLLM continuous batching, kernel
  versions, and hardware all perturb temperature-0 output, so
  "identical source → identical archive bytes" cannot be promised model-side.
  Options:
  - **(a) Summary lockfile — recommended.** Persist `content_hash → summary`
    as a build input (the prototype's resumable JSONL already has this shape).
    Rebuilds reuse cached summaries byte-for-byte; the model runs only for
    new/changed chunks. Reproducibility becomes a property of the lockfile,
    not the model; rebuilds stay fast and offline; incremental cost is solved
    for free.
  - (b) Weaken D5 to "reproducible given identical model + engine + config"
    for LLM-summarized packs. Honest but a weaker guarantee and hard to
    verify.
- [ ] **Failure semantics.** Endpoint unreachable, or a single chunk fails
  after retries: hard-fail the build, or fall back to the heuristic summary
  for that chunk? A silent mixed pack ships inconsistent quality invisibly.
  Lean: fail-by-default; partial fallback only via an explicit opt-in flag.
  Includes degenerate-output guards — length cap, empty check (the prototype
  only checks empty), refusal/preamble detection.
- [ ] **Provenance in the manifest.** Record summarizer strategy, model id,
  and prompt version in `manifest.json` so consumers can see summaries are
  LLM-generated and by what. The prompt text must be versioned — editing it
  regenerates every summary and churns every pack digest, so a prompt change
  must be deliberate and visible.
- [ ] **Stemmer keep/revert (entangled — decide before prod).**
  `tokenize='porter unicode61'` + `_migrate_fts_tokenizer()` is live in
  `src/synd/storage/db.py` today and migrates user DBs whether or not a
  decision is recorded. The matrix says porter is a wash under enrichment
  (and under RRF), and standalone it carries the 9-question direct-tier
  regression. Decide explicitly: revert to unicode61 (simplest, matches the
  evidence) or keep. Leaving it undecided means shipping a tokenizer
  migration by accident.
- [ ] **Config surface and the locality promise.** Endpoint/model/key via env
  vars (as the prototype does) vs CLI flags. Document explicitly that
  build-time LLM calls go to a *publisher-controlled* endpoint: this does not
  violate "no outbound network calls at query time," but corpus content does
  leave the build process for whatever endpoint the publisher configures —
  the docs must say so since "all data stays local" is a headline promise.

## 3. Engineering work

Follows mechanically once §2 is settled; blocked primarily on the
reproducibility decision.

- [ ] **Move generation into `synd.builder` behind `--summarizer llm`.**
  Port `enrich_summaries.py` (prompt, greedy decoding, concurrency,
  `chat_template_kwargs: {"enable_thinking": false}`) into the builder,
  honoring the deterministic sorted-walk chunk ordering. Default remains the
  heuristic summarizer — `llm` is opt-in.
- [ ] **Summary lockfile/cache implementation** (assuming §2 option (a)):
  location, format, invalidation on prompt-version change, interaction with
  the builder's write-then-rewrite `pack_digest` flow.
- [ ] **Tests** (project bar: valid input, invalid input, boundaries, failure
  modes):
  - endpoint unreachable / HTTP error / malformed response
  - empty and oversized summary handling
  - lockfile cache hit / miss / prompt-version invalidation
  - digest stability across two builds with a warm cache
  - fixture pack asserting heuristic remains the default with no flag
  - dedicated exception subclasses (`SyndError` family) + CLI exit-code
    mapping for summarizer failures
- [ ] **Docs**: D3 revisit-clause closure entry in `decisions.md`; build docs
  for the flag; cost expectations (~70 min cold for a 6.5k-chunk corpus on a
  local 27B at concurrency 16, seconds warm from the lockfile); locality
  disclosure from §2.
- [ ] **Cleanup**: retire or clearly mark the eval-side prototype scripts as
  superseded once the builder path exists; the eval matrix should then be
  runnable against packs built by the real `--summarizer llm` flag (never
  bypass the public API).
