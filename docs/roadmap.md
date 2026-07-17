# Synaptic Drift — Semver Roadmap

## Current Focus — v0.4.0 "Smarter Search"

v0.1.1, v0.2.0, and **v0.3.0 (the general web crawler) are shipped** (tagged).
This branch is **v0.4.0 "Smarter Search"** (a minor bump — new `--summarizer llm`
CLI option and new manifest fields): the D30 evidence ladder and the D31
build-time LLM summary enrichment it justified.

**In this release:**
- **D30** — the hybrid-search evidence ladder: L1/L2/L3 eval harness, two gold
  corpora (`html_v1`, `pilot_v1`), the RRF matrix prototype, and the measured
  sequencing (enrichment first, weighted RRF second, porter stemmer rejected as
  a wash). See `docs/decisions.md` D29–D31 and `docs/hybrid-search.md`.
- **D31** — `synd build --summarizer llm`: build-time LLM summary enrichment
  (append format, v1 prompt, content-hash summary lockfile for reproducibility,
  fail-hard semantics, manifest provenance). Porter stemmer reverted to
  unicode61 in the same change.

**Next up** (see "v0.4.0" below and `docs/spikes.yaml` S14/S15): run the model-size
sweep (the eval program's headline deliverable), then decide weighted-RRF step 2 /
reranking / BGE-M3 on the sweep evidence.

**Deferred:**
- **Pre-built packs + the pack registry → v0.4.1 "Distribution"** (below). The
  crawler that *builds* the packs shipped in 0.3.0; publishing/resolving follows.
- **PyPI release** — the publish step is written but unreachable on the automated
  path (see the Release section below for the trigger gap and fix).

---

## v0.1.0 — "MVP" ✓

**Theme**: Working end-to-end implementation. Build, verify, pull, query.

**Status**: Tagged. Not on PyPI (blocked — see v0.2.0).

- [x] `synd build` — source tree → `.ctx` pack (Markdown/HTML, lexicographic walk, deterministic chunk IDs)
- [x] `synd verify` — 8-step archive safety validator, policy enforcement, `pack_digest` integrity check
- [x] `synd pull` — verify-before-import, atomic SQLite transaction, WAL mode
- [x] `synd query` — FTS5 BM25 search with source attribution
- [x] MCP server — `query-docs` and `resolve-deps` tools over stdio
- [x] Policy engine — lifecycle state gating (`draft` / `approved` / `deprecated` / `revoked`)
- [x] CI workflow — lint, typecheck, test on push/PR
- [x] Release workflow — builds wheel + `.ctx` packs on `v*` tags, creates GitHub release
- [x] fastmcp@3.3.0 `.ctx` pack — first release artifact (1190 chunks)
- [x] mypy clean — builder type errors resolved

---

## v0.1.1 — "Bug Fixes + Benchmarks"

**Theme**: Fix data integrity bugs found post-tag; ship benchmark infrastructure.

- [x] Polish README — "implementation is beginning" replaced with accurate status
- [x] Expose `limit` parameter on `query-docs` MCP tool and `query_docs()`
- [x] Token overhead benchmark harness — `tests/benchmarks/test_token_overhead.py` with baseline at `tests/benchmarks/results/v0.1.0.json`
- [x] GitHub Actions benchmark workflow — PR delta comparison via `tests/benchmarks/compare.py`
- [x] WebFetch vs Synaptic Drift benchmark — `tests/benchmarks/test_webfetch_vs_synd.py` with fastmcp fixture
- [x] Extend PR comment bot to include WebFetch vs Synaptic Drift results alongside token overhead
- [x] Benchmark output cleanup — PR comment redesigned with plain-English headline table and collapsed detail; raw JSON dump replaced with formatted standalone output. Console output unchanged (runs under `-s`, not in reviewers' way).
- [x] Implement or remove unused `max_tokens` parameter in `src/synd/server.py`
- [x] Docs cleanup — consolidate `.work/` artifacts, merge `todo.md` into `roadmap.md`, migrate gotchas to `CLAUDE.md`, absorb `ultraplan` findings into canonical docs
- [x] Build and ship mcp@2025-11-25 as pack #2 for the v0.1.1 release artifact — `mkdir /tmp/mcp-docs && curl -o /tmp/mcp-docs/mcp.md https://modelcontextprotocol.io/llms-full.txt && synd build mcp@2025-11-25 --source /tmp/mcp-docs --output ./packs`

---

## v0.2.0 — "First Users"

**Theme**: Make it effortless to start. Polish the rough edges that stop adoption.

### Completed

- [x] **MCP two-tool refactor** — replace `query-docs` (single tool with `detail` parameter) with separate `search` (summaries + chunk IDs) and `fetch` (full content by ID) tools. Enforces the two-step agent pattern structurally.
- [x] **`synd serve` CLI command** — `synd serve` launches the MCP stdio server, discoverable from `synd --help`. Replaces the undiscoverable `python -m synd.server` invocation.
- [x] **MCP documentation refresh** — `docs/MCP.md` rewritten with accurate `search`/`fetch` API; all config examples updated to `synd serve`; `README.md` MCP snippet updated with `cwd`.
- [x] **FTS5 heading_path + BM25 weight tuning** — `heading_path` added as first column in `chunks_fts` with 2.5× weight; BM25 tuned to heading 2.5× > summary 1.5× > content 1.0×.

### Foundation — no blockers, start now

- [x] **`schemas/manifest.v2.schema.json`** — machine-readable JSON Schema as single source of truth for manifest fields; wire verifier to validate against it. Establishes a stable schema contract before PyPI release.
- [x] **Cross-platform path handling** — normalize to forward slashes, reject backslashes/UNC in validator. Modify `src/synd/validator/verify.py`
- [x] **Error message polish** — every error path produces an actionable message. Audit all `SyndError` subclass usage
- [x] **Lockfile in git** — `synd.lock` at project root, written by `synd add`; commit to version-control documentation dependencies analogous to `Cargo.lock`
- [x] **`synd add` (renamed from `synd pull`)** — `synd pull` was misleading (implies remote fetch; only imports local files). Renamed to `synd add`, consistent with `cargo add`, `uv add`, `npm install <pkg>`. `synd pull` kept as a hidden deprecated alias. See `decisions.md` D19.
- [x] **`synd sync`** — reads `synd.lock`, skips already-imported packs (idempotent), verifies digest against lockfile before importing (supply-chain check), imports any missing packs. Enables `git clone && synd sync` workflow. HTTPS `source_url` fetch deferred until URL fetcher module lands (exits with actionable `FetchError`). See `src/synd/cli/sync.py`.
- [x] **`synd remove`** — removes a pack from `index.db` and rewrites `synd.lock`. Completes the verb set: without it, removing a pack requires hand-editing the lockfile. See `src/synd/cli/remove.py`.

### Chunker quality stream — S7 → chunker → S2 → summary

- [x] **Custom markdown chunker** — replace chunkana with a `markdown-it-py`-backed chunker that splits at all heading levels (`#` through `######`), keeps code fences atomic, and builds `heading_path` accurately by construction. Removes the `##`-only limitation that produces 900-token multi-section chunks. See `decisions.md` D14.
  - Replace `src/synd/builder/chunking.py`; remove chunkana from dependencies; add `markdown-it-py>=3.0`
- [x] **Heading-aware summary heuristic** — prefix chunk summaries with the leaf heading node (`"STDIO Transport: STDIO is the default transport..."` instead of `"You can now run this server..."`). Eliminates false-positive summaries for chunks that open with transitional sentences or code. See `decisions.md` D13.
  - Modify `generate_summary()` in `src/synd/builder/chunking.py`; no schema changes
- [x] **MDX Tab body de-indentation and pipeline order fix** — three cooperating bugs caused Mintlify tutorial pages (`build-server`, `build-client`) to produce single 15k–22k token chunks instead of per-section splits. See `decisions.md` for details.
  - `strip_mdx` was running before `unwrap_jsx_blocks`, destroying `<Tab>…</Tab>` pairs before they could be processed
  - `unwrap_jsx_blocks` was leaving Tab inner content 4-space indented; `markdown-it-py` parsed headings and prose as `code_block` tokens
  - Fences inside Tab bodies had indented closing `\`\`\`` lines (4+ spaces); CommonMark requires ≤3 spaces on a closing fence, so `markdown-it-py` never closed them and treated the rest of the page as fence content
  - Fixed in `src/synd/builder/mdx.py`: swap pipeline order, `textwrap.dedent()` in `unwrap_jsx_blocks`, `_INDENTED_FENCE_CLOSE_RE` in `_extract_code_fences`
  - Result: `build-server` 22,496t → 111 chunks (max 1,065t); `build-client` 15,172t → 99 chunks (max 651t)
- [x] **Chunk size tuning** — `--max-chunk-tokens` (default: 800), `--min-chunk-tokens` (default: 20), and `--warn-chunk-tokens` (default: 2× max) CLI params on `synd build`. Default max raised from 500 → 800 for code-heavy SDK docs. Oversized-chunk warnings emitted at build time. See [S9](docs/spikes.yaml), [D24](docs/decisions.md).
  - Modified `src/synd/builder/chunking.py`, `src/synd/builder/build.py`, `src/synd/cli/build.py`
  - Added `scripts/validate_chunk_sizes.py` for real-production-data validation
- [x] **Minimum-token merge** — chunker-internal guard that suppresses stub chunks (heading-only, <20 tokens) by skipping the emit when a heading boundary would produce below-threshold content. The suppressed content carries forward and is absorbed by the next section naturally. Eliminates ~249 stubs from the MCP pack without a separate post-processing pass. See [S10](docs/spikes.yaml), [D23](docs/decisions.md).
  - Modified `src/synd/builder/chunking.py`: added `_DEFAULT_MIN_CHUNK_TOKENS = 20`, `min_chunk_tokens` param on `chunk_content()`
- [x] **Tab heading disambiguation** — chunks produced by expanding Mintlify `<Tabs>` blocks carry identical `heading_path` values across all language tabs. BM25 cannot distinguish "Python / Implementing tool execution" from "TypeScript / Implementing tool execution" because the tab title is discarded during unwrapping.

  **Reproduction** (requires a built MCP pack):
  ```bash
  synd build mcp@2025-11-25 --source https://modelcontextprotocol.io/llms-full.txt --output /tmp/mcp-pack
  unzip -p /tmp/mcp-pack/mcp@2025-11-25.ctx chunks.jsonl | python3 -c "
  import json, sys
  from collections import Counter
  chunks = [json.loads(l) for l in sys.stdin if l.strip()]
  bs = [c for c in chunks if 'build-server' in c['source_url']]
  paths = Counter(c['heading_path'] for c in bs)
  for path, cnt in paths.most_common(5):
      print(f'x{cnt}  {path}')
  "
  # Output: x15  docs/develop/build-server / Testing your server with Claude for Desktop
  #         x8   docs/develop/build-server / Building your server
  #         x8   docs/develop/build-server / Building your server / Running the server
  # build-server has 111 chunks but only 45 unique heading_paths (7+ language tabs × shared structure)
  ```

  **Root cause**: `unwrap_jsx_blocks` in `src/synd/builder/mdx.py` discards the `title` attribute of `<Tab title="Python">` when extracting the inner content. The chunker receives a flat document with no language context; all 7 language tabs' sections share the same `heading_path`.

  **Recommended solution**: in `unwrap_jsx_blocks`, when handling a `<Tab title="...">` element (not `<Tabs>` or other block tags), extract the `title` attribute and rewrite the Tab body so that all headings inside are shifted one level deeper, then inject the title as a heading at the level of the Tab's shallowest heading. Example:

  ```
  Before (inside <Tab title="Python">):
    ### Importing packages
    ### Implementing tool execution

  After unwrapping with title injection (shallowest was ###, so inject ## Python,
  shift ### → ####):
    ## Python
    #### Importing packages
    #### Implementing tool execution
  ```

  This produces `heading_path = "docs/develop/build-server / Building your server / Python / Implementing tool execution"`, fully disambiguating all language tabs. The depth-shift ensures the tab title stays in scope across all sections within the tab, rather than being immediately popped by a same-level sibling heading.

  Implementation sketch:
  1. Parse `title` from `<Tab title="...">` — add a named group to `_JSX_UNWRAP_RE` or handle `Tab` specially before the generic unwrap loop
  2. Detect the minimum heading level in the Tab body (e.g. H3)
  3. Shift all headings in the body one level deeper (H3 → H4, H4 → H5)
  4. Prepend `## {title}\n\n` (one level above the shifted headings) to the dedented body
  5. Non-`Tab` tags (Note, Warning, Tabs, Frame, etc.) are unaffected — only `Tab` injects a heading

  Modify: `src/synd/builder/mdx.py` (`unwrap_jsx_blocks`); add tests in `tests/test_builder/test_mdx.py` covering single Tab, multi-Tab, and nested Tab > Note.

  - Implemented in `src/synd/builder/mdx.py`: `_unwrap_tab_block()` replaces the inline lambda in `_JSX_UNWRAP_RE.sub()`. Extracts `title` via `_TAB_TITLE_RE`, detects shallowest heading level, shifts all body headings one deeper (capped at H6), injects title as heading at `shallowest - 1` level. No-title falls back to plain dedent. See D22.
  - Result: `build-server` 45 → 112 unique `heading_path` values (111 chunks); `build-client` 105/105 unique paths (perfect).

### URL fetch stream — S6 → llms-full.txt → (S8 in parallel) → llms.txt → packs

- [x] **`synd build --source <url>/llms-full.txt`** — fetch a `llms-full.txt` URL, preprocess it into per-page documents, chunk and build a `.ctx` pack.
  - *[S6](docs/spikes.yaml) done — `html_to_markdown()` in `src/synd/builder/fetch.py`, `markdownify` in core deps.*
  - `src/synd/builder/build.py` accepts URL sources via `build_pack_from_url()`
  - `src/synd/builder/fetch.py` — `fetch_text()` and `fetch_page()` using `urllib.request`; content routing: `.md` → `process_mdx()`, else → `html_to_markdown()`
  - `src/synd/builder/llms_full.py` — Mintlify-aware preprocessor:
    - Split on `Source: <url>` boundary lines to recover individual pages
    - Strip MDX/JSX tags (e.g. `<FeatureBadge />`, `<Note>`, `<McpClient>`, `<Icon />`, inline `<sup><a ...>`) — keep inner text, discard component wrappers
    - Use each `Source:` URL as the page `source_url`; derive page title from the first `#` heading
    - Feed resulting per-page documents into the existing chunker individually so `heading_path` values are page-relative and meaningful
  - `src/synd/builder/mdx.py` — `process_mdx()` pipeline: strip MDX imports/exports, unwrap JSX blocks, clean headings, collapse blank lines. See D21.
- [x] **`synd build --source <url>/llms.txt`** — fetch `llms.txt` index, fetch each linked page individually, chunk and build a `.ctx` pack. Higher quality than `llms-full.txt`: each page is fetched individually, giving page-relative heading paths and clean structure. Basic rate limiting + `User-Agent`.
  - *[S6](docs/spikes.yaml) done. [S8](docs/spikes.yaml) done — see D21.*
  - **Mintlify behaviour**: `llms.txt` on Mintlify sites already contains `.md` URLs (no URL manipulation needed). Fetching them returns MDX directly — no HTML-to-markdown conversion required. JSX components (`<Frame>`, `<Note>`, `<Tabs>`, `<Warning>`, etc.) must still be stripped; inner text kept, wrappers discarded. Images inside `<Frame>` are discarded.
  - For non-Mintlify sites (ReadTheDocs, Docusaurus, etc.): HTML fetch → `html_to_markdown()` via markdownify + BeautifulSoup4
  - Use the page URL as `source_url`; derive title from first `#` heading
- [x] **URL noise filtering** — exclude changelog/release/news pages from `synd build --source <url>` builds. Segment-level path matching avoids false positives (e.g. `/configuration-updates.md`).
  - `src/synd/builder/url_filter.py`: `DEFAULT_NOISE_URL_PATTERNS`, `is_noise_url()`, `filter_page_urls()`
  - `build_pack_from_url()` in `build.py` gains `excluded_url_patterns` param (defaults to `DEFAULT_NOISE_URL_PATTERNS`)
  - CLI: `--exclude-url-pattern` (repeatable, appends to defaults) and `--no-url-filter` (disables all filtering)
### FTS5 tuning — parallel, no blockers

- [x] **FTS5 search quality** — two remaining improvements:
  - [x] Query sanitization: FTS5 special characters stripped in `fts.py` (prevents crashes on `mcp.tool` style queries)
  - [x] Query preprocessing: stopword filtering, term normalization — `_preprocess_query()` in `fts.py` filters common English function words before the FTS5 MATCH
  - [ ] Synonym expansion: `auth` → `authentication`, `JWT` → `JSON Web Token`, etc. — **deferred indefinitely** (superseded by hybrid search contingency; see `decisions.md` D25)
- [x] **Query latency benchmark** — `tests/benchmarks/test_query_latency.py` measures P50/P95 against 100,427 real documentation chunks (59 packs); results in `tests/benchmarks/results/latency.json`. Measured P95 (2500 reps/query): rare terms <0.2ms, multi-term ~5ms, common single term ~10ms, high-limit common term ~20ms. See `docs/search-benchmarks.md`.

### Release — after foundation + S5

- [ ] **PyPI release** (`pip install synaptic-drift`, `pip install synaptic-drift[serve]`) — the packaging conflict is resolved ([S5](docs/spikes.yaml) — `mcp` is an optional `[serve]` extra, base install has no server dependency), and a `pypa/gh-action-pypi-publish` (OIDC trusted publisher) `publish` job already exists. The actual remaining blocker is a **CI trigger gap**, plus metadata gaps and an external prerequisite:
  - **Orphaned publish job (the real blocker).** The `publish` job lives only in `release.yml`, which fires on `push: tags: v*`. But the automated release path (`cut-release.yml` → PR → merge → `auto-release.yml`) creates the release *and its tag* via the GitHub API using `GITHUB_TOKEN`. GitHub's loop-prevention policy means a `GITHUB_TOKEN`-created tag does **not** trigger `on: push: tags` workflows — so `release.yml` never fires and nothing is uploaded to PyPI. `auto-release.yml`'s header comment confirms the no-double-fire behaviour is intentional, but the side effect is that publishing is unreachable.
    - **Recommended fix:** move/add the `publish` job into `auto-release.yml` (it already builds `dist/` in-job; append an OIDC publish job with `environment: name: pypi`). Then `release.yml` can either be retired or kept solely for manual PAT-driven tag pushes. *Do not* switch the automated path back to a PAT-pushed tag — that reintroduces the loop-prevention bug `auto-release.yml` was created to fix.
  - **Metadata gaps.** `pyproject.toml` has no `description`, `readme`, `license`, `authors`, `classifiers`, or `[project.urls]`. The package uploads but renders a blank PyPI page. Fill these before the first publish.
  - **External prerequisite (cannot be done from the repo).** Configure a PyPI Trusted Publisher on pypi.org for `synaptic-drift`, scoped to repo `trydydd/synaptic-drift`, the **workflow filename that runs `publish`** (must match wherever the job ends up — `auto-release.yml` under the recommended fix), and environment name `pypi`. Trusted publishers are bound to a specific workflow filename, so this must be kept in sync with the fix above. A pending-publisher entry can be created before the project's first release.
- [x] **Validator optimization** — `compute_pack_digest()` now hashes each ZIP entry's content directly in filename-sorted order, eliminating the full in-memory ZIP reconstruction. The previous implementation read the entire archive into memory and rebuilt a second in-memory ZIP — decompressing and re-compressing every file — solely to zero out `pack_digest` before hashing, allocating 500MB+ near the archive limit. See `src/synd/builder/manifest.py:compute_pack_digest`; merged in PR #38.

### Discovery — after PyPI release + pre-built packs

- [ ] **`synd init`** — scan project deps, download pre-built packs, configure MCP server
  - New module: `src/synd/cli/init.py`
  - Parse `requirements.txt`, `pyproject.toml`, `package.json`, `Cargo.toml`
  - Map package names to `.ctx` pack URLs (static JSON registry on GitHub)
  - Generate MCP config (`.cursor/mcp.json` or Claude Code equivalent)

---

## v0.3.0 — "Growth"

**Theme**: Multi-user, multi-project, CI-integrated. Start looking like infrastructure.

- [x] **`synd build --source <url>`** — general web crawler: fetch and chunk all reachable pages from a docs site root. For sites without `llms.txt` or `llms-full.txt`. Rate limiting, `robots.txt` compliance (with `--no-robots` escape hatch), configurable `--user-agent`, `--max-pages` cap with truncation provenance in the manifest. No embeddings or JS rendering — static HTML only. See `decisions.md` D28.
  - `src/synd/builder/crawler.py` — BFS link-following seeded by sitemaps (robots `Sitemap:` directives → `<root>/sitemap.xml` → `<host>/sitemap.xml`, sitemapindex recursion; sitemaps seed but never replace link-following — RTD sitemaps list only version roots), host+path-prefix scoping, canonical-URL dedup, per-host robots cache honoring `Crawl-delay`
  - `src/synd/builder/fetch.py` — `extract_links()`, `fetch_html()` (Content-Type + redirect-aware), User-Agent threading
  - Crawled pages sorted by canonical URL before chunk-ID assignment (deterministic builds); crawl provenance (`crawl_pages_fetched`/`crawl_truncated`/`crawl_max_pages`) recorded in the manifest
- Pre-built packs and the pack registry are deferred to **v0.4.1 — "Distribution"** (below). 0.3.0 ships the crawler; distribution follows once there is something to distribute.
- [ ] **CI/CD templates** — GitHub Actions, GitLab CI, CircleCI: build packs on release, verify in PRs, publish to static registry
- [ ] **Pre-built packs for top 100 libraries** — scale up pack-building CI pipeline
- [ ] **Token budget intelligence** — `max_tokens` on `search`/`fetch` controls response size, balancing breadth vs. depth within the budget
- [ ] **`index-deps` MCP tool** — scans project deps, reports which have packs available, which are indexed, which are stale
- [ ] **Staleness detection** — compare indexed pack versions against project lockfiles. Surface warnings via `index-deps` MCP tool
- [ ] **Structured logging** — JSON logging at key checkpoints. `python logging` with configurable verbosity

---

## v0.4.0 — "Smarter Search"

**Theme**: Improve retrieval on vocabulary-mismatch and paraphrase queries that
tuned FTS5 cannot reach. Gate every rung on measurement, not schedule.

**Trigger (met, 2026-07)**: the `html_v1` and `pilot_v1` gold corpora reproduced
vocabulary-mismatch recall@1–20 = 0.000 (n=26) and paraphrase recall@5 = 0.000
(n=98) against real crawled docs — evidence tuned FTS5 cannot address. See
`docs/decisions.md` D25/D29/D30, `docs/hybrid-search.md`.

**Design note**: an earlier draft proposed BGE-M3 dense+sparse vectors computed
at `synd add` time as *the* design. The measured program landed differently —
build-time enrichment first, then a weighted-RRF vector leg using a tiny
pure-ONNX encoder (all-MiniLM-L6-v2, no PyTorch), embeddings shipped inside the
`.ctx` at build time. BGE-M3 is now an open evaluation question, not an
assumption (see S15).

### Shipped in 0.4.0

- [x] **Build-time LLM summary enrichment** (D31). `synd build --summarizer llm`:
  append-format summaries, v1 prompt, content-hash summary lockfile for
  reproducible republishing, fail-hard semantics, manifest provenance. Strict
  improvement on both gold corpora; zero query-time dependency. Porter stemmer
  evaluated and reverted (a wash under enrichment). See D30/D31,
  `docs/document-processing.md`.
- [x] **Evaluation harness** — the D30 L1/L2/L3 evidence ladder, `html_v1` +
  `pilot_v1` gold corpora, and the RRF matrix prototype under `tests/evals/`.

### Next (post-0.4.0)

- [ ] **Model-size sweep** — the eval program's headline deliverable. Run
  L2/L3 (agent competence + endtask A/B) across the target model sizes
  (Qwen3 {0.6B, 4B, 8B, 14B, 30B-A3B} + the served 27B) with enrichment on, to
  answer how small a calling model can still use `synd` effectively. The L2
  finding that the residual failure is the model's *selection* among surfaced
  results — not retrieval — makes this the input that gates the remaining
  decisions. Harness: `docs/eval-harness-design.md` e14/e15.
- [ ] **Weighted RRF + `sqlite-vec`** — the vector leg, prototyped and measured
  in `tests/evals/l1_rrf_matrix.py` (enriched rrf-w3: html direct recall@5 0.964,
  paraphrase off the floor). Ship as `synaptic-drift[semantic]`: build-time
  MiniLM embeddings bundled in the `.ctx`, `sqlite-vec` ANN at query time, RRF
  fusion. Model-free BM25-only remains the byte-for-byte fallback when no encoder
  is present. Gate the go/no-go on sweep results.
- [ ] **[S14] Query-time reranking — feasibility** — whether a local optional
  ONNX cross-encoder closes the rank-1 gap Context7 holds, or whether the D12
  summary-scan workflow already suffices (esp. for small sweep models). Ship /
  defer-until-sweep / reject with numbers. See `docs/spikes.yaml` S14.
- [ ] **[S15] BGE-M3 dense+sparse evaluation** — does BGE-M3 (dense +
  learned-sparse, if a PyTorch-free ONNX path exists) beat the current
  unicode61-BM25 + MiniLM-dense stack enough to justify its weight? The
  learned-sparse leg is the interesting case for the vocab-mismatch tier. Feeds
  which encoder the vector leg ships with. See `docs/spikes.yaml` S15.

### Deferred cleanup

- [ ] Retire the eval-side prototype scripts (`tests/evals/generation/
  enrich_summaries.py`, `build_enriched_artifacts.py`) and re-point the matrix
  at packs built by the real `--summarizer llm` flag (never bypass the public
  API).
- [ ] Pilot-corpus enrichment ship-readiness — the pilot result is measured
  (D30 pilot addendum) but building real pilot packs via the shipped flag is
  untested end to end.

---

## v0.4.1 — "Distribution"

**Theme**: Publish and resolve pre-built packs, now that the crawler can produce them.

- [ ] **Pre-built packs for top 20 libraries** — the crawler covers the 19 packages without `llms.txt` (pydantic builds from its llms.txt; boto3 is out of acceptance scope — subtree recipe documented instead). Acceptance harness: `scripts/build_top20_packs.py`. See `docs/top20-python-packages.md`. Publish as GitHub Releases.
- [ ] **Pack registry (static hosting)** — `synd add fastapi@0.115.0` resolves against a registry index (JSON manifest on CDN or GitHub Pages). No auth. Read-only.
  - New module: `src/synd/registry/` (client only; server is a static file host)
  - `synd add` accepts `package@version` in addition to file paths

---

## v1.0.0 — "Enterprise-Ready"

**Theme**: Trust, governance, and operational maturity. The version you'd sell to an enterprise security team.

- [ ] **Schema migrations** — `PRAGMA user_version`-based forward-only migrations. Modify `src/synd/storage/db.py`. Must land before any new column additions.
- [ ] **Real signature verification** — Step 8 currently only checks file existence. Implement ed25519 or Sigstore. Modify `src/synd/validator/verify.py`, add `src/synd/signing/`
- [ ] **Observability** — health endpoint for HTTP transport, query latency metrics, import audit trail. Modify `src/synd/server.py`
- [ ] **Multi-project support** — configurable `.synd/` location, monorepo workspace support
- [ ] **Policy profiles** — per-team/per-workspace policy overrides
- [ ] **Audit logging** — who imported what, when, from where. New `audit_log` table in `index.db`
- [ ] **Backup and recovery** — `synd rebuild --from-lockfile`
- [ ] **Comprehensive documentation** — man pages, API reference, enterprise deployment guide
