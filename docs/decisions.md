# Synaptic Drift — Decision Log

Decisions made during architecture and pre-implementation planning, with reasoning and rejected alternatives. This document exists so that future contributors (human or agent) don't re-open settled questions.

## Format

Each entry records: the decision, the alternatives considered, why we chose what we chose, and when it can be revisited.

---

## D1: Implementation Language — Python

**Decision**: implement Synaptic Drift entirely in Python.

**Alternatives considered**:
- **Rust**: single-binary distribution, memory safety in archive validator, 10-50x faster normalization/hashing. Rejected because: 2-3x slower development velocity, less mature MCP SDK, higher contributor barrier for the target audience (enterprise teams), and `chunkana` is Python-only (would need rewrite or PyO3 bridge defeating single-binary goal).
- **Hybrid (Python + Rust hot paths)**: Python for orchestration, Rust for validator and normalizer via PyO3. Rejected because: premature optimization. Sub-10ms FTS5 queries are achievable in pure Python. Ship MVP, measure, optimize only if performance is actually a problem.
- **Go**: good CLI story, single binary. Rejected because: no MCP SDK, no chunkana equivalent, weaker ecosystem for the specific libraries needed.

**Revisit when**: performance profiling shows Python is the bottleneck for large documentation sets (thousands of pages), or if the Rust MCP SDK matures significantly.

---

## D2: Python Version — 3.11+

**Decision**: require Python 3.11 or later.

**Alternatives considered**:
- **Python 3.10**: would require `tomli` backport for TOML parsing. 3.10 is in security-fix-only mode, EOL October 2026. Adds a dependency for diminishing benefit.
- **Python 3.12+**: too restrictive. 3.11 is widely available in enterprise environments and gives us `tomllib` in stdlib plus `str | None` union syntax.

**Revisit when**: Python 3.11 reaches EOL (October 2027).

---

## D3: Summary Generation — Heuristic

**Decision**: generate one-line summaries heuristically at build time. First sentence for prose chunks, leading function/class signature for code-heavy chunks.

**Alternatives considered**:
- **LLM-generated summaries**: more accurate, better natural language. Rejected because: adds runtime dependency (API key or local model), costs money per build, makes builds non-deterministic (same input → different output), requires network (violates local-first constraint), and increases build time dramatically.
- **No summaries (heading_path only)**: simpler, zero generation logic. Rejected because: the progressive disclosure pattern relies on summaries to help agents decide which chunks to expand. Heading paths alone are often too terse (`API / Users` tells you nothing about what's in the chunk).

**Revisit when**: the `summary` field schema supports upgrading the strategy without a format change. A future `--summarizer llm` flag could opt into LLM generation for users who want it.

---

## D4: source_url Convention — Relative Paths for Local Builds

**Decision**: `source_url` is always populated, never null. Local builds store the relative path from the `--source` argument (e.g. `--source ./docs` + `auth/oauth.md` = `docs/auth/oauth.md`). Only leading `./` is stripped.

**Alternatives considered**:
- **file:// URLs**: absolute paths like `file:///home/user/project/docs/auth/oauth.md`. Rejected because: makes `.ctx` packs completely unportable. The pack would only make sense on the machine where it was built.
- **Paths relative to source root (stripping the source dir)**: `auth/oauth.md` instead of `docs/auth/oauth.md`. Rejected because: loses useful context. If someone sees `auth/oauth.md` they have to guess where that lives in the project. `docs/auth/oauth.md` is unambiguous.
- **Nullable source_url with fallback logic**: allow null, fall back to `packages.source_url` at query time. Rejected because: adds query-time complexity and branching. Always-populated is simpler for both the implementation and consumers.

**Revisit when**: Phase 2 adds web crawling; crawled builds will use full `https://` URLs in the same field. No format change needed.

---

## D5: pack_digest Zeroing — Empty String

**Decision**: when computing `pack_digest`, set the field value to `""` in manifest.json, hash the archive bytes, then write the real digest back.

**Alternatives considered**:
- **Remove the key entirely**: cleaner semantics ("field doesn't exist yet"). Rejected because: removing and re-adding a JSON key introduces key-ordering sensitivity. Different JSON libraries may serialize remaining keys in different orders, breaking the hash. Would require canonical JSON serialization (sorted keys) at both build and verify time — an additional subtle correctness requirement.
- **Sidecar file (`.ctx.sha256`)**: hash the archive directly, store digest externally. Rejected because: two files to track, easy to lose the hash file, breaks the self-contained single-file property of `.ctx` packs.
- **Hash everything except manifest**: `pack_digest` covers only `chunks.jsonl` + `pages.json`. Rejected because: doesn't detect tampering of manifest metadata (someone could change `lifecycle_state` from `draft` to `approved` undetected).
- **Don't store digest inside the archive**: store it only in the lockfile and packages table after import. Rejected because: you can't verify a `.ctx` file in isolation — you need an external source of truth to check against.

**ZIP entry timestamps**: all entries in a `.ctx` archive are written with a fixed `date_time` of `(2021, 8, 8, 0, 0, 0)`. The build writes the archive twice — once with `pack_digest: ""` to compute the digest, then again with the real digest. Without pinned timestamps, the two writes could land in different 2-second DOS timestamp buckets, producing different ZipInfo metadata and making the verifier unable to reproduce the hash. The pinned date is arbitrary and must never change (it is baked into every `.ctx` file ever produced).

**Revisit when**: never. This is a format-level decision that's baked into the verification sequence.

---

## D6: Token Counting — Character Heuristic

**Decision**: `token_count` is computed as `len(content) // 4`. Documented clearly as an approximate estimate for budget planning.

**Alternatives considered**:
- **tiktoken (cl100k_base)**: exact counts for Claude/GPT-4 class models. Rejected because: adds a ~2MB dependency to `synaptic-drift[build]`, and the "right" tokenizer varies by model (cl100k, o200k, etc.). Since `token_count` is advisory — agents use it to estimate "will this fit?" not for exact accounting — the heuristic is good enough.
- **Multiple tokenizer counts** (generic + Anthropic + OpenAI): maximum accuracy across models. Rejected because: ~30 bytes of bloat per chunk in `chunks.jsonl` for accuracy that doesn't change any decision. An agent with 2000 tokens of budget doesn't care if a chunk is 387 or 412 tokens.

**Revisit when**: agents start making tight budget decisions based on `token_count` and the ~20% error margin causes problems. The field can be recomputed at build time without a format change.

---

## D7: HTML Handling — Markdown-First for MVP

**Decision**: Markdown is the primary supported format. `.html` files are accepted (extension whitelist) but processed via basic tag removal only. Boilerplate stripping (nav, footer, breadcrumbs, sidebar) is deferred to Phase 2.

**Alternatives considered**:
- **Tag-based stripping with element/class matching**: strip `<nav>`, `<footer>`, `<aside>`, elements with class matching `breadcrumb`, `sidebar`, etc. Rejected for MVP because: the primary use case is local documentation which is overwhelmingly Markdown. Building a robust HTML boilerplate detector before we have real crawled HTML to test against is speculative. Phase 2's crawler will produce real HTML that informs the stripping rules.
- **Reject HTML entirely in MVP**: only accept `.md` files. Rejected because: some projects keep docs as HTML (e.g. generated API docs). Basic tag removal is trivial to implement and better than refusing the files.

**Revisit when**: Phase 2 crawler implementation begins and we have real-world HTML documentation to inform stripping rules.

---

## D8: Source Tree Handling — Recursive with Lexicographic Sort

**Decision**: `synd build --source <path>` recurses subdirectories by default. Files are discovered by extension whitelist (`.md`, `.html`, `.htm`), sorted lexicographically by full relative path. This sort order determines chunk ID assignment and `normalized_content_hash`.

**Alternatives considered**:
- **Flat only (no recursion)**: process only top-level files in the source directory. Rejected because: real documentation is almost always nested (`docs/api/`, `docs/guides/`, etc.). Requiring users to flatten their docs first is a poor UX for zero benefit.
- **Configurable recursion (`--no-recurse`, `--include`, `--exclude`)**: full flexibility. Rejected for MVP because: adds CLI complexity without clear demand. Deferred — can be added later without breaking anything.
- **Filesystem iteration order (unsorted)**: rely on `os.listdir` / `pathlib.iterdir()` order. Rejected because: iteration order is filesystem-dependent (arbitrary on Linux, sometimes alphabetical on macOS/Windows). Same source tree would produce different hashes on different platforms, breaking the integrity guarantee.

**Revisit when**: users request `--no-recurse` or glob-based filtering. The architecture accommodates these as additive features.

---

## D9: Error Model — Emergent via TDD

**Decision**: start with a base `SyndError` class. Discover and add specific exception subclasses during TDD as failure modes emerge from writing tests.

**Alternatives considered**:
- **Define full exception hierarchy up front**: lay out every exception class and exit code before writing code. Rejected because: speculating about failure modes without implementation experience leads to either over-engineering (exceptions that never get raised) or gaps (real failures that don't fit the pre-defined classes). TDD naturally surfaces the real failure modes.

**Revisit when**: after MVP implementation is complete, review the exception hierarchy for consistency and document the final exit code mapping.

---

## D10: Test Fixtures — Static Archives + Pytest Factories

**Decision**: use static fixtures in `tests/fixtures/` for integration tests (sample source trees, known-good `.ctx` packs, malformed archives) and pytest factory functions for unit tests (individual chunks, normalization cases, policy evaluation).

**Alternatives considered**:
- **Static fixtures only**: committed test data for everything. Rejected because: unit tests for normalization and chunking need many small variations that would bloat the fixtures directory. Factories are more expressive for parameterized tests.
- **Programmatic fixtures only**: build all test data in code. Rejected because: archive validation tests need real `.ctx` files with known byte-level properties (corrupted archives, path traversal entries, bad signatures). These are hard to construct programmatically and easier to inspect as static files.

**Revisit when**: the test suite grows large enough that fixture management becomes a problem.

---

## D11: FTS5 Configuration — Minimal for MVP, Tuning Partially Implemented (v0.2.0)

**Original MVP decision**: ship with raw query passthrough, uniform BM25 column weights, default tokenizer, and `heading_path` stored in `chunks` but not indexed in FTS5.

**v0.2.0 status**:

- ✅ **`heading_path` added to `chunks_fts`** — `db.py:48-52` creates the FTS5 table with `heading_path` as the first column; both triggers updated to include it. `section_tags[0]` from chunkana provides the `##`-level heading; `###` depth requires D14 (custom chunker).
- ✅ **BM25 column weights tuned** — `fts.py:67` uses `bm25(chunks_fts, 2.5, 1.5, 1.0)` (heading 2.5×, summary 1.5×, content 1.0×). Weight only activates when `heading_path` is non-null; FTS5 treats NULL as empty string for fallback-chunked documents.
- ✅ **Query sanitization** — `fts.py` strips FTS5 special characters (`.`, `(`, `)`, `"`, `*`, etc.) before passing to `MATCH`, preventing syntax errors on symbol-heavy queries like `mcp.tool`.
- ✅ **Stopword filtering and term normalisation** — `_preprocess_query()` in `fts.py` filters common English function words (articles, auxiliary verbs, prepositions) before the query reaches FTS5. Words that appear in section titles (`how`, `what`, `where`) and FTS5 boolean operators (`AND`, `OR`, `NOT`) pass through unchanged. Falls back to original sanitized query if all tokens are stopwords. Tested in `tests/test_search/test_fts.py`.
- ⬜ **Synonym expansion** — `auth` → `authentication`, `JWT` → `JSON Web Token`. **Intentionally deferred indefinitely.** Synonym dictionaries are a maintenance burden and address vocabulary mismatch — the same problem that hybrid search (v1.1 contingency) would solve via embeddings. Building a synonym table now would be superseded if hybrid search lands, and is low-value if it does not (FTS5 BM25 IDF already down-weights high-frequency generic terms). See `docs/hybrid-search.md`.
- ⬜ **Custom tokenizer** — porter stemmer or unicode61 with diacritics removal. Still deferred; low-priority for technical docs where exact terms dominate.

**Schema commitment note**: indexing `heading_path` in FTS5 means future chunker changes (D14) that alter how `heading_path` is computed will require rebuilding the FTS5 index (or a migration). This is acceptable — the index can be rebuilt from the `chunks` table on schema version bump.

**Latency benchmark**: measured against a real 100,116-chunk index built from 59 documentation packs (https://directory.llmstxt.cloud/); results in `tests/benchmarks/results/latency.json`. Rare technical terms: P95 <1ms. Common single terms (`install`): P95 ~11ms. Multi-term intersections: P95 ~6ms. High-limit on common term (limit=20): P95 ~23ms.

---

## D12: MCP Tool Surface — Single Tool vs Split ✓ Implemented (v0.2.0)

**Decision**: ship MVP with a single `query-docs` tool accepting a `detail` parameter (`"summary"` or `"full"`); split into separate `search`/`fetch` tools in v0.2.0.

**The problem with the single-tool surface**: `detail="full"` sounds better than `detail="summary"` to an LLM agent. The parameter name nudged agents toward the expensive single-step path — fetching full content speculatively without a prior relevance pass — which is the footgun the two-step pattern is designed to prevent.

**Resolution (v0.2.0)**:

```
search   query, packages, limit, max_tokens  → always returns summaries + chunk_ids only
fetch    chunk_ids, max_tokens               → always returns full content by ID
```

This enforces the two-step pattern architecturally: `search` cannot return full content; `fetch` cannot do speculative full-content search. Eliminates the footgun without any stateful enforcement. Implemented in `src/synd/server.py`; documented in `docs/MCP.md`.

**`max_tokens` default rationale**: `max_tokens` defaults to `None` (no budget enforcement) by design. A default of e.g. `4000` would silently trade away recall — with BM25 noise, the most relevant chunk can land at position 8 or 12, and a tight budget would exclude it with no signal to the agent. `limit` is the right knob for controlling result count; `max_tokens` is an explicit opt-in for agents with known token constraints.

---

## D13: Summary Heuristic — First Sentence vs Heading-Aware Generation

**Decision**: generate summaries by extracting the first non-trivial sentence from chunk content.

**The problem**: this fails when a chunk opens with a code block or a short bridging sentence rather than a topic-describing sentence. Observed in the fastmcp stdio benchmark:

| Chunk | Summary generated | What the chunk actually covers |
|---|---|---|
| 2 | "You can now run this MCP server by executing `python my_server." | STDIO is the default transport |
| 3 | "STDIO is ideal for: * Local development..." | STDIO transport section |
| 5 | "We recommend using HTTP transport instead of SSE for all new projects." | SSE deprecation + CLI reload |

Chunk 2's summary gives an agent scanning for "stdio configuration" no signal that this chunk is the relevant one. The missed chunk contains the correct answer.

**Proposed fix (deferred)**: prefix the summary with the leaf heading node.

```
summary = "<leaf heading>: <first prose sentence>"
```

For chunk 2 under `### STDIO Transport (Default)`:
```
STDIO Transport (Default): STDIO (Standard Input/Output) is the default transport for FastMCP servers.
```

`heading_path` is already computed before `_generate_summary()` is called in `src/synd/builder/chunking.py`; it just isn't passed through. The change is additive — `summary` remains a plain string, no schema impact.

**Edge cases**: preamble chunks (no heading) fall back to first-sentence behaviour; top-level chunks where heading equals the page title skip the prefix to avoid redundancy; headings over ~60 chars use only the leaf node; chunks opening with a list or code block skip to the next prose sentence.

**Revisit when**: v0.2.0 chunker work begins. Depends on D14 — if chunkana is replaced with a heading-boundary chunker, `heading_path` will be accurate by construction and the prefix heuristic becomes more reliable.

---

## D14: Chunker — chunkana Replacement: Custom Chunker on markdown-it-py

**MVP decision**: use chunkana for MVP structural chunking, accepting its limitations.

**chunkana verdict**: does not support heading-based splitting at arbitrary depth. The `structural` strategy splits only at `##` level, keeping all `###` subsections together. `header_path` is always `[]`; Synaptic Drift works around this by reading `section_tags[0]`, but this is the ceiling of what chunkana can provide. Observed impact: in the fastmcp benchmark, chunk 5 spans six `###` sections (932 tokens) and is matched by FTS5 on incidental keyword overlap rather than relevance.

**Library survey (S1 — done)**: four production-stable (≥1.0.0) candidates evaluated:

- **chunknorris 1.2.2** — meets all requirements (all heading levels, heading_path as ordered list by construction, code fences atomic, paragraph overflow splitting) but ships parsers for PDF, Word, Excel, and Jupyter Notebooks Synaptic Drift will never use. PyMuPDF, pandas, matplotlib in the dependency tree. ~30MB install footprint for a markdown chunker.
- **langchain-text-splitters 1.1.2 `MarkdownHeaderTextSplitter`** — code fences atomic, all heading levels configurable, but heading_path returns as a flat dict (requires reconstruction glue) and no paragraph overflow splitting. Requires `langchain-core`.
- **semantic-text-splitter 0.30.1** — pre-1.0, no heading hierarchy output. Eliminated.
- **chonkie 1.6.7** — delimiter-based, no structural heading tracking. Eliminated.

**Decision**: build a custom chunker using `markdown-it-py` 3.0.0+ as the parsing backend.

**Rationale**: neither off-the-shelf library is a clean drop-in. `markdown-it-py` is MIT-licensed, zero additional dependencies, actively maintained, and is already a widely-used CommonMark parser. The chunknorris markdown chunker is ~250 lines of reference implementation; Synaptic Drift's equivalent with `markdown-it-py` tokens gives full control over heading_path construction, code-fence atomicity, and paragraph overflow with no dependency weight penalty. This aligns with Synaptic Drift's local-first, minimal-dependency philosophy.

**What the custom chunker must do**:
- Split at heading boundaries at all levels (`#`, `##`, `###`, `####`, and deeper) as the primary split point
- Treat fenced code blocks as atomic — never split mid-fence
- Split oversized sections at paragraph boundaries when a section exceeds `max_chunk_tokens`
- Build `heading_path` accurately by construction as a `/`-joined string of ancestor heading texts
- Replace `src/synd/builder/chunking.py` — the `process_file()` function and `generate_summary()` call site

**`semchunk` ruled out earlier**: general-purpose recursive delimiter splitter with no markdown heading awareness. Token-counter-driven rather than structure-driven.

**Implemented in v0.2.0** (`feature/chunker`). See spike S7 (status: done) for implementation notes. The S2 heading-aware summary heuristic (D13) was implemented in the same pass — `generate_summary()` now accepts `heading_path` and prefixes the summary with the leaf heading node.

---

## D15: Pack #2 — mcp@2025-11-25

**Decision**: ship the Model Context Protocol spec (`mcp@2025-11-25`) as the v0.1.1 release artifact alongside fastmcp@3.3.0.

**Rationale**: Synaptic Drift depends on `mcp` directly, and the MCP tool split (D12) is the largest single v0.2.0 work item — agents using the `search`/`fetch` tools will query this pack constantly. `modelcontextprotocol.io` publishes `llms-full.txt`, making it buildable today without a crawler or HTML extraction.

**Alternatives considered**:
- **httpx@0.28.1**: still pre-1.0 (0.x), no API stability commitment. Rejected.
- **requests**: stable (2.x), good candidate for HTTP client docs, but uses RST source and no llms-full.txt — requires S6 HTML extraction work first.
- **click / rich**: Synaptic Drift's other deps, but their docs are sparse and less queried by agents.

**Source**: `modelcontextprotocol.io/llms-full.txt` (spec version 2025-11-25). Build command:
```
synd build mcp@2025-11-25 --source https://modelcontextprotocol.io/llms-full.txt --output ./packs
```

**Revisit when**: never — this is a release artifact decision. Future packs follow the same evaluation process.

---

## D16: Lockfile location — `synd.lock` at project root

**Decision**: The lockfile lives at `synd.lock` in the project root, not `.synd/index.lock`.

**Rationale**: All established package managers (`Cargo.lock`, `package-lock.json`, `poetry.lock`, `Pipfile.lock`) place the lockfile at the project root. A root-level file is immediately visible, committed without gitignore exceptions, and signals its purpose to any developer who opens the repo. `.synd/index.lock` required a `!.synd/index.lock` gitignore negation which silently fails if the parent directory rule uses `/` rather than `/*` — a correctness hazard that tripped us in practice (B1 in the code review).

**Alternatives considered**:
- **`.synd/index.lock`**: Keeps all Synaptic Drift state under one directory but requires gitignore gymnastics and is invisible at the root level. Rejected.

**Revisit when**: Never — this is a UX convention decision. The location is now baked into `add.py`, docs, and `synd.lock` itself.

---

## D17: `pack_source` vs `source_url` — two distinct URL fields

**Decision**: The `packages` table carries two separate URL fields: `source_url` (from the manifest — where the documentation was authored) and `pack_source` (set at import time — where the `.ctx` file was fetched from).

**Lockfile `source_url` population** (refined in D19): the lockfile's `source_url` field prefers the manifest's `source_url` when it is an HTTPS URL (canonical distribution address for official packs), and falls back to `pack_source` (the local import path) otherwise. This ensures `synd sync` can fetch official packs by URL while still recording a usable path for locally-built packs.

**Rationale**: These are fundamentally different things. `source_url` in the manifest is provenance metadata about the documentation content (e.g., `docs/api`). `pack_source` is the local path where the `.ctx` was imported from. For official packs built with a canonical HTTPS `source_url` in their manifest, that URL is what `synd sync` needs to re-fetch the pack. Conflating them caused the lockfile to show the build-time docs directory as the fetch location, making `synd sync` impossible to implement correctly.

**Alternatives considered**:
- **Overwrite `source_url` with the pull path**: Destroys provenance metadata. Rejected.
- **Store pull path only in the lockfile, not the DB**: Loses the data if the lockfile is regenerated. Rejected.
- **Always use `pack_source`**: Would record `/tmp/fastmcp@3.3.0.ctx` in the lockfile, breaking `synd sync` for official packs. Rejected.

**Revisit when**: Phase 2 registry design — `pack_source` may evolve to carry a structured registry reference rather than a raw URL.

---

## D18: Manifest validation — JSON Schema (jsonschema library, draft/2020-12)

**Decision**: `manifest.json` validation in the verifier (step 2) uses a machine-readable JSON Schema file at `src/synd/schemas/manifest.v2.schema.json`, validated via the `jsonschema` Python library. Schema uses draft/2020-12.

**Rationale**: The previous manual field-presence check (`_REQUIRED_MANIFEST_FIELDS` loop) only caught missing keys — it passed manifests with `chunks: "bad"` or `lifecycle_state: "active"`. JSON Schema validates types, enums, patterns, and numeric constraints in one declaration that is also human-readable and tooling-compatible. The schema file becomes the single source of truth for the manifest contract, referenced in docs and validated in CI.

**`additionalProperties` is not set to `false`**: forward-compatible with Phase 2/3 field additions (`embedding_model`, etc.) without a schema version bump.

**Alternatives considered**:
- **Pydantic model**: heavier dependency, more code, harder to publish as a standalone schema artefact. Rejected for MVP.
- **Manual field-by-field checks**: already in place, insufficient. Replaced.

**Revisit when**: schema version 3 (Phase 2 crawl fields) or Phase 3 (embedding fields) — add new optional properties to the schema, keep `additionalProperties` open.

---

## D19: CLI command set — `add`, `sync`, `remove` replace/extend `pull`

**Decision**: Rename `synd pull` → `synd add`; implement `synd sync`; add `synd remove`. Keep `synd pull` as a hidden deprecated alias for one minor version.

**Rationale**:
- `synd pull` was misleading: "pull" implies a remote fetch (`git pull`, `docker pull`), but the command only imports a local file. The misnaming becomes actively harmful once `synd sync` lands and *does* fetch.
- `synd add` is the correct verb: it is consistent with `cargo add`, `uv add`, `npm install <pkg>`, and reads correctly with any input source (local path, HTTPS URL, future registry spec).
- `synd sync` closes the "nothing reads `synd.lock`" gap. It enables `git clone && synd sync` to reproduce the local index on a fresh checkout — the primary missing workflow for teams.
- `synd remove` completes the verb set. Without it, removing a pack requires hand-editing the lockfile, which breaks the invariant that the lockfile is always written by the CLI.

**Command surface after this change (8 commands, two personas)**:

| Persona  | Commands |
|----------|----------|
| Consumer | `add`, `sync`, `remove`, `query`, `serve` |
| Author   | `build`, `verify`, `inspect` |

No individual user touches all 8. Consumer persona needs ~4 in normal use (`sync`, `serve`, `query`, sometimes `add`).

**`add` vs `sync` kept separate** (not collapsed à la `npm install [pkg]`): the operations are genuinely different — ad-hoc acquisition of a new pack vs. reproducing the full index from the lockfile. The uv/Cargo discipline of one-verb-one-thing holds here.

**`synd.toml` deferred**: without a registry there is no resolution step that would distinguish a manifest from a lock. The lockfile continues to serve as both declaration and receipt until the Phase 3 static registry introduces real version ranges. At that point, `synd.toml` + `synd.lock` split cleanly along the Cargo/uv model.

**Alternatives considered**:
- **`synd install [<ref>]` (npm-style unification)**: `npm install` (no args) = from lock; `npm install <pkg>` = add. Familiar but conflates two distinct operations. Broadly considered a design mistake in npm. Rejected in favour of explicit verbs.
- **`synd import`**: accurate but unused by any major package manager. Less discoverable. Rejected.
- **Drop `synd verify` from the top-level**: too useful for CI pipelines ("verify this .ctx before importing"). Kept as standalone; it is also already implicit in `add` and `sync`.

**Revisit when**: Phase 3 static registry lands and `synd.toml` is introduced — at that point `synd add <pkg@range>` becomes the primary declaration verb and `synd sync` becomes the "ensure lockfile is satisfied" executor, matching the Cargo model exactly.

---

## D20: HTML-to-Markdown Conversion — markdownify + BeautifulSoup4

**Decision**: use `markdownify` (MIT) with `BeautifulSoup4` (MIT) for converting rendered HTML documentation pages to markdown for the chunker. Added to the `[serve]` optional extra in `pyproject.toml`.

**Pipeline** (implemented in `src/synd/builder/fetch.py`):
1. Parse with `BeautifulSoup(html, "html.parser")`
2. Decompose boilerplate elements: `nav`, `header`, `footer`, `aside`, `script`, `style`, `noscript`
3. Extract main content: `<main>`, `role="main"`, or `<article>` — fall back to `<body>` if none found
4. Convert with `markdownify.markdownify(target, heading_style="ATX")`
5. Strip pilcrow anchor links (`¶`) left by ReadTheDocs/Sphinx heading anchors
6. Collapse runs of blank lines

**Alternatives evaluated** against `requests.readthedocs.io/en/latest/user/quickstart/`:

- **trafilatura (Apache 2.0)**: good content extraction but inline code spans get fragmented — backtick-enclosed text broken by newlines in output, corrupting prose for FTS and chunking. Rejected.
- **html2text (MIT)**: zero dependencies, but outputs indented (4-space) code blocks rather than fenced ` ``` ` blocks. Incompatible with the chunker's fence detection in `normalizer.py`. Rejected.
- **scripts/llms_full_to_markdown.py (stdlib only)**: handles MDX source (Mintlify `.md` endpoints, `llms-full.txt`) via `strip_mdx()` + `MarkdownExtractor`. Not suitable for rendered browser HTML — no content extraction, nav bleeds through as plain text. Kept in `scripts/` as reference; `strip_mdx()` and `_extract_code_fences()` are candidates for promotion to `src/synd/builder/mdx.py` in S8.

**BeautifulSoup4 is already a `markdownify` transitive dependency** — no additional install cost. `markdownify` is a core dependency (not `[serve]`) because URL fetch is a `synd build` feature, not an MCP server feature.

**Revisit when**: S8 implementation encounters a site type where the BeautifulSoup content-element selector (`<main>`, `<article>`, `role="main"`) produces poor results. Add site-specific selector logic to `html_to_markdown()` at that point.

---

## D21: URL Fetch Pipeline — `urllib.request`, Markdown output, Regex MDX stripping (S8)

**Decision**: the `synd build --source <url>/llms.txt` pipeline uses `urllib.request` (stdlib) for HTTP, produces CommonMark markdown as its output format, and strips MDX/JSX via regex (not a full JSX parser).

**Five sub-decisions and rationale**:

| Question | Decision | Rationale |
|---|---|---|
| Output format | CommonMark markdown | Preserves heading structure (`##`, `###`) that the S7 chunker uses to build `heading_path`. Converting to plain text loses this signal entirely. Same format as local-file pipeline — same chunker handles both paths. |
| HTTP library | `urllib.request` (stdlib) | Zero additional dependency. `httpx` ruled out — still pre-1.0 (same reasoning as D15 which rejected it for Pack #2). Pattern already established in `scripts/llms_full_to_markdown.py`. Async not needed: builder is synchronous; if async/pooling becomes necessary in the v0.3.0 crawler, that's the right time to re-evaluate. |
| Content-type routing | URL ends in `.md` → MDX path; otherwise → HTML path | Mintlify-hosted sites already expose `.md` URLs in their `llms.txt` (confirmed in S8 context). Fetching a `.md` URL returns MDX directly — no HTML-to-markdown step needed. Non-`.md` URLs serve rendered HTML and go through D20's `html_to_markdown()` pipeline. |
| MDX stripping approach | Regex (not full JSX parser) | Covers the confirmed Mintlify tag set (`<Note>`, `<Warning>`, `<Tip>`, `<Tabs>`, `<Tab>`, `<Frame>`, `<Icon />`, `<FeatureBadge />`, etc.) without additional dependencies. A full JSX/XML parser would add complexity for marginal correctness gain on a known, finite tag set. |
| Rate limiting | Configurable `sleep`, default 0.5s between pages | Simple; correct for sequential single-threaded fetches. Token bucket deferred — not needed until the v0.3.0 crawler introduces concurrent fetching. |

**New modules** (implemented as part of S8):
- `src/synd/builder/mdx.py` — `strip_mdx()`, `unwrap_jsx_blocks()`, `clean_heading()`, `process_mdx()`. Functions promoted and extended from `scripts/llms_full_to_markdown.py`.
- `src/synd/builder/llms_full.py` — `LlmsPage`, `parse_llms_txt()`, `fetch_pages()`. Parses the `[label](url)` link format from a `llms.txt` index and orchestrates per-page fetching.
- `src/synd/builder/fetch.py` extended with `fetch_page(url, *, rate_limit_sleep)` — unified entry point that routes on URL extension.

**`pyproject.toml`**: no change — `urllib.request` is stdlib.

**Revisit when**: the v0.3.0 crawler needs concurrent fetching (add connection pooling / async at that point); or a new site type is encountered where `.md` extension detection misfires (extend routing heuristic or add explicit `Content-Type` check).

---

## D23: Minimum-Token Stub Elimination — Internal Chunker Guard

**Decision**: eliminate stub chunks (heading-only content, typically < 10 tokens) by suppressing the emit inside `chunk_content()` when a heading boundary would produce below-threshold content, rather than running a separate post-chunking merge pass.

**The problem**: a heading immediately followed by another heading (no prose between them) produces a stub chunk whose content is just the heading markdown line. These stubs:
- Contribute nothing to BM25 that `heading_path` doesn't already carry.
- Inflate result counts with near-zero-information entries.
- Cannot be surfaced usefully by `fetch` — the content is the heading.

The MCP pack had 249 such stubs across 2,089 chunks.

**Mechanism**: when `heading_open` is encountered, the would-be chunk content (`source_lines[chunk_start_line:heading_line]`) is evaluated against `min_chunk_tokens`. If it is below the threshold, `_emit()` is not called and `chunk_start_line` is not advanced. The `ancestor_stack` is still updated so the heading becomes part of the ancestry. At the next emit, `chunk_start_line` still points to the stub's heading line, so its content is prepended to the absorbing section's content naturally.

**Why internal rather than post-process**: the stub never exists as an intermediate object. The chunker makes the merge decision at the same point it decides where to split — heading boundary detection — which is the semantically correct place. A post-processing pass would need to re-evaluate content that the chunker already visited, and it would require all callers to know to run the pass.

**`heading_path` after absorption**: the absorbing section's (deeper) heading path is used. The stub's heading text appears in the merged chunk's content, so BM25 sees it via content scoring. The ancestor node is also present in the absorbing chunk's `heading_path` as an ancestry entry, so the 2.5× heading weight is preserved.

**Threshold**: `_DEFAULT_MIN_CHUNK_TOKENS = 20`. Pure stubs are almost always < 10 tokens; legitimate one-sentence intros before a code block are 15–30 tokens. 20 absorbs all stubs without risk of merging real introductory content. Configurable via `min_chunk_tokens` parameter on `chunk_content()`.

**Disabling**: pass `min_chunk_tokens=0` to get the original behaviour (all chunks emitted including stubs).

**Alternatives considered**:
- **Post-chunking merge pass (S10 original proposal)**: separate `merge_stubs()` function applied after `chunk_content()` returns. Rejected because: the merge decision belongs at the split point; it adds an external pass that callers must invoke; stubs exist as intermediate `RawChunk` objects even if briefly.
- **Merge backward (absorb into previous chunk)**: when a stub is the last chunk on a page, there is no forward absorber. Backward merging produces worse chunks — the previous section's summary gains the stub heading out of context. Forward absorption with natural carry-forward avoids this entirely for all but the all-stubs-on-page edge case, which is handled by the final `_emit(len(source_lines))`.

**Revisit when**: evidence shows that legitimate short introductory sections (e.g. a 3-sentence overview before a subsection) are being absorbed when they shouldn't be. Lowering `_DEFAULT_MIN_CHUNK_TOKENS` to 10 or making it configurable via CLI are both straightforward changes.

---

## D22: Tab Heading Disambiguation — Title Injection + Heading Depth Shift

**Decision**: when `unwrap_jsx_blocks()` processes a `<Tab title="...">` element, inject the `title` attribute as an ATX heading one level above the shallowest heading in the Tab body, and shift all body headings one level deeper (capped at H6). Tags other than `<Tab>` (i.e. `<Tabs>`, `<Note>`, `<Warning>`, `<Frame>`, etc.) are unaffected — they still receive plain `textwrap.dedent`.

**Problem**: Mintlify wraps multi-language tutorials in `<Tabs><Tab title="Python">…</Tab><Tab title="TypeScript">…</Tab></Tabs>`. Before this fix, `unwrap_jsx_blocks` extracted the inner text and discarded the `title` attribute. The downstream chunker received a flat document where all language tabs concatenated their headings without any language label. Every language produced a chunk for `### Implementing tool execution` and all chunks got the same `heading_path`, making BM25 unable to distinguish "Python / Implementing tool execution" from "TypeScript / Implementing tool execution".

**Algorithm**:
1. Extract `title` from `title="..."` or `title='...'` (via `_TAB_TITLE_RE`). If absent, fall back to plain `textwrap.dedent` — no heading injected.
2. `textwrap.dedent` the body (strips common leading whitespace from Mintlify's 4-space indented Tab content).
3. Scan body lines for ATX headings (`#`–`######`); find the minimum `#` count (shallowest level).
4. Shift every heading one level deeper (`##` → `###`, `#####` → `######`; `######` stays `######`).
5. Prepend `{'#' * (shallowest - 1)} {title}\n\n` to the shifted body. If no headings exist, prepend `### {title}\n\n`.

**Why depth-shift instead of just prepending?** A plain prepend would put the title at the same level as the first heading inside the tab (`## Python\n\n## Setup\n\n...`). The chunker would immediately pop `Python` from the ancestor stack when it hit `## Setup`, so only the first section would carry the language label in its `heading_path`. Depth-shifting ensures the title stays in scope for the entire tab body.

**Alternative considered**: a plain prepend without shifting. Rejected because it breaks the ancestor-stack invariant: the injected heading is immediately shadowed by the first same-level heading inside the tab.

**Edge cases**:
- No `title` attribute → plain dedent (backward compat).
- Body has no headings → inject `### {title}` (default H3).
- Shallowest heading is H6 → shift is a no-op; inject `##### {title}`.
- Title contains special markdown characters (e.g. `C#`) → injected literally; no escaping.
- `<Tabs>` container → plain dedent, no title injection.

**Measured result**: `build-server` page of `mcp@2025-11-25` went from 45 unique `heading_path` values (111 chunks) to 112 unique values. `build-client` achieved 105/105 unique paths.

**Implementation**: `_unwrap_tab_block()` in `src/synd/builder/mdx.py` replaces the inline lambda in `_JSX_UNWRAP_RE.sub()`.

**Revisit when**: a non-Mintlify doc site uses a different multi-tab component name (add to the `_JSX_UNWRAP_RE` tag list and handle in `_unwrap_tab_block` if title injection is appropriate); or heading depth semantics change (unlikely).

---

## D23: Minimum-Token Stub Elimination — Internal Chunker Guard

*(recorded inline above; D23 is `min_chunk_tokens` implementation in S10)*

---

## D24: Chunk Size Gate — Warn-Only Strategy; Default max_chunk_tokens Raised to 800

**Decision**:
1. `_DEFAULT_MAX_CHUNK_TOKENS` raised from 500 → **800** tokens.
2. A new warning system detects chunks that exceed a configurable `warn_chunk_tokens` threshold (default: `2 × max_chunk_tokens = 1,600`) **after** all splits. Warnings are emitted by `cli/build.py`; no changes to `chunk_content()` return type.
3. No automatic splitting of structural token types (`code_block`, `table`, long `fence`). Warn-only.
4. Three new `synd build` CLI params: `--max-chunk-tokens`, `--min-chunk-tokens`, `--warn-chunk-tokens`.

**Why raise the default to 800?** Synaptic Drift's primary target corpus is technical developer documentation (SDK references, API guides, code-heavy tutorials). The original 500-token default was set before real production packs existed. Analysis of the MCP pack (P95 ≈ 423t at 500t max) confirmed that 95% of sections already fit comfortably. However, for developer docs, a prose section of 600–800 tokens typically represents a complete concept explanation that should stay intact for coherent retrieval; splitting it forces the LLM agent to assemble context from two adjacent chunks. Industry tools targeting code-heavy docs commonly use 800–1,024 tokens. The real-data validation script (`scripts/validate_chunk_sizes.py`) confirms the distribution at both defaults.

**Why warn-only (no automatic split for structural tokens)?** Three token types bypass the paragraph-overflow split: `code_block` (4-space indented content, e.g. changelog pages), `table`, and very long `fence` blocks. Automatic line-boundary splitting of these would produce incoherent fragments:
- Indented code block split mid-line: arbitrary cut, no code-structural boundary.
- Table split mid-row: continuation chunks lack the header row, breaking semantic context.
- Fenced code split mid-function: violates the fence-atomicity invariant that is required for correct code-example retrieval.

The URL noise filter (`DEFAULT_NOISE_URL_PATTERNS`) already handles the canonical production case (changelog pages). Warn-only gives pack authors actionable information without risking worse retrieval quality from forced splits.

**`warn_chunk_tokens` formula**: defaults to `2 × max_chunk_tokens` (not a fixed constant). This scales sensibly when authors tune `--max-chunk-tokens` (e.g. `--max-chunk-tokens 300` warns at 600t). The 2× multiplier leaves room for legitimate large fenced code examples (which legitimately bypass the prose split) while still catching true blobs.

**Alternatives considered**:
- Automatic split of `code_block` / `table` at line boundaries — rejected (incoherent fragments, see above).
- Hard cap with fence-breaking split — rejected (breaks fence-atomicity invariant; a code example cut mid-function is worse than one large chunk).
- Keeping `max_chunk_tokens = 500` — rejected (too conservative for code-heavy SDK docs; evidence from MCP/FastMCP production packs).

**Implementation**:
- `src/synd/builder/chunking.py`: `_DEFAULT_MAX_CHUNK_TOKENS = 800`; `chunk_file()` now forwards `max_chunk_tokens` and `min_chunk_tokens` to `chunk_content()`.
- `src/synd/builder/build.py`: `build_pack()` and `build_pack_from_url()` accept all three params; `_finalize_pack()` detects oversized chunks and returns `tuple[Path, list[RawChunk]]`.
- `src/synd/cli/build.py`: three new `@click.option` decorators; `_print_oversized_warnings()` formats the warning output.
- `scripts/validate_chunk_sizes.py`: live validation against MCP and FastMCP `llms-full.txt`.

**Revisit when**: real user feedback shows that oversized structural-token chunks (tables, code blocks) materially degrade search quality in a way that automatic splitting would fix without worse side effects; or when the warning system generates enough data about which structural bypasses are most common to design a targeted mitigation.

---

## D25: Synonym Expansion — Deferred Indefinitely

**Decision**: do not implement a synonym expansion system (e.g. `auth` → `authentication`, `JWT` → `JSON Web Token`).

**Rationale**: synonym expansion was listed as a pending v0.2.0 FTS5 tuning item but its cost/benefit doesn't justify implementation:

1. **BM25 IDF already handles frequency** — FTS5's inverse document frequency component naturally down-weights tokens that appear in many chunks. High-frequency synonyms (`api`, `auth`) already have low IDF weight, so adding `auth → authentication` would boost exactly the noisy matches BM25 is already trying to suppress.
2. **Direct overlap with hybrid search** — The vocabulary mismatch problem (a user queries `JWT` but docs say `JSON Web Token`) is precisely what the v1.1 hybrid search contingency addresses via embedding-based semantic similarity. Building a hand-curated synonym dictionary solves a narrow slice of the same problem at higher maintenance cost and without the generalization.
3. **Maintenance burden** — A synonym dictionary requires ongoing curation as the documentation corpus grows. Different domains need different synonym rules; a single table cannot generalize. This cost compounds forever.
4. **The contingency design is deliberate** — hybrid search (v1.1) is gated on evidence of real vocabulary-mismatch failures. Until that evidence arrives, FTS5 is sufficient. Building the thing hybrid search would supersede preemptively violates the "no premature optimization" principle.

**Alternatives considered**:
- **Hand-curated domain dictionary** — highest quality for known terms, but brittle, domain-specific, and immediately superseded if hybrid search lands.
- **Automatic synonym generation via WordNet or similar** — adds a dependency, produces noisy expansions for technical terms (WordNet knows `token` as a travel pass, not an API credential).
- **Query rewriting via LLM** — introduces an LLM dependency at query time, breaking the local-first constraint.

**Revisit when**: v1.1 hybrid search contingency is triggered (real evidence of vocabulary-mismatch failures that tuned FTS5 cannot address). If hybrid search lands, this question is permanently closed. If hybrid search doesn't land, revisit synonym expansion only with concrete query failure data as motivation.

---

## D26: FTS5 Latency Benchmark — Real Corpus Required

**Decision**: the FTS5 latency benchmark must use a real documentation corpus, not a synthetic one.

**Background**: the initial implementation used a synthetic corpus with a 70-word vocabulary and uniform random term assignment. This gave P50 latencies of 44–78ms. Replacing it with 100,116 real documentation chunks (59 packs from `directory.llmstxt.cloud`) gave P50 latencies of 0.15–21ms depending on query type.

**Why synthetic corpora mislead for FTS5**: FTS5 BM25 query time is O(posting-list-size) — proportional to how many chunks match the query term(s). A 70-word vocabulary with uniform distribution gives every term a ~44% document frequency, so every query scans ~44,000 matching chunks in a 100K corpus. Real documentation vocabulary has a long-tail distribution: most technical terms (class names, method names, config keys, product-specific jargon) appear in < 1% of chunks. The benchmark's most important case — a specific technical term from a library's API — completes in < 1ms, not 78ms. A synthetic corpus produces worst-case numbers that misrepresent the dominant query pattern.

**Implication**: whenever FTS5 performance is re-measured (e.g. after an FTS5 configuration change, or after a Python/SQLite version upgrade), the benchmark must be run against the real corpus. The 100ms P95 regression guard in `tests/benchmarks/test_query_latency.py` is calibrated for real data; running it against a synthetic corpus would produce false failures.

**Setup**: `python scripts/build_benchmark_packs.py` fetches and builds the 59-pack corpus. Already-built packs are skipped, so re-running after a partial build is safe.

**Revisit when**: never for the core methodology. The corpus list in `scripts/build_benchmark_packs.py` should be refreshed periodically as sites update their `llms-full.txt` content and as new sites are added to `directory.llmstxt.cloud`.

## D27: Search API Return Type — SearchResponse Over Bare List

**Decision**: `search()` returns `SearchResponse(results, query_used)`, not `list[SearchResult]`. All inputs that produce no searchable terms raise `SearchError`. The MCP server omits `query_used` from the wire format when it equals the raw input (no preprocessing effect).

**Background**: returning a bare `list[SearchResult]` is permanently ambiguous. The caller cannot distinguish: valid query with empty index, valid query with no matches, empty input, all-special-chars input, or all-stopwords input. Every major search API (Elasticsearch, Algolia, Typesense, MeiliSearch) uses a structured response type for this reason — the response object communicates what happened, not just what was found. Spike S12 confirmed this pattern is universal.

**Fields chosen**:
- `results: list[SearchResult]` — the paginated hit set. Empty list unambiguously means FTS5 ran and matched nothing, because all other empty-result paths now raise before returning.
- `query_used: str` — the query string after preprocessing (sanitization + stopword filtering) that was actually issued to FTS5. Distinct from the caller's raw input when preprocessing removed terms. Enables callers and agents to understand what was searched.

**Fields rejected**:
- `total: int` — all major APIs include a total count (before pagination). We excluded it because FTS5 does not return a total without a second `COUNT(*)` query, and `len(results) == total` for our use case (no separate pagination from FTS5). Callers use `len(response.results)`.
- `took_ms: float` — useful for diagnostics but adds tokens to every MCP response. Deferred.

**MCP serialization**: the server layer serializes `query_used` only when it differs from the raw input query. When no preprocessing occurred, the field is omitted, keeping the common-case MCP response identical in size to the previous bare-list format. This is the key design point: internal richness does not require wire-format bloat.

**Contract**: `SearchError` is raised for (1) empty input, (2) input that sanitizes to empty (all special chars), (3) input that filters to empty (all stopwords). `SearchResponse` is returned only for inputs that reach FTS5 — `results=[]` then means exactly one thing.

**Revisit when**: FTS5 is replaced by a hybrid backend, at which point `total` (before-limit count from the underlying engine) becomes cheap to obtain and worth including.

## D28: General Web Crawler — Sitemap-Seeded Link-Following, robots.txt by Default, Crawl Provenance in the Manifest

**Decision**: `synd build --source <url>` treats any HTTP(S) URL that does not end in `llms.txt`/`llms-full.txt` as a docs-site root and crawls it (`src/synd/builder/crawler.py`). Design points:

- **Discovery is BFS link-following seeded by sitemaps**: robots.txt `Sitemap:` directives, then `<root>/sitemap.xml`, then `<host>/sitemap.xml` (with `<sitemapindex>` recursion) seed the frontier; links are always followed from every fetched page. Sitemaps are never trusted as the complete page set — live validation showed ReadTheDocs serves a host sitemap listing only version roots (`/en/latest/` …), so sitemap-exclusive crawling silently produced a 1-page pack. Seeding still captures sitemap-only pages unreachable by links (dedup makes the overlap free), and a malformed or missing sitemap degrades to plain BFS. Static HTML only; no JS rendering.
- **Scope** is the root's host plus directory path prefix (the wget `--no-parent` equivalent). URLs are canonicalized before dedup: fragment and query stripped, trailing `/index.html` folded to the directory, scheme/host lowercased, trailing-slash variants collapsed.
- **robots.txt is honored by default** via `urllib.robotparser`, fetched with the crawl's own User-Agent (`RobotFileParser.read()` would send Python's default UA, which some docs hosts 403). Unreachable robots.txt = allow-all, per convention. A disallowed root raises `CrawlError`; disallowed individual pages are skipped and counted. `Crawl-delay` is honored when larger than `--rate-limit`. **`--no-robots` ships as an explicit escape hatch** — the wget workaround this replaces (`build-pack-html.sh`) always ran `robots=off`, and some public docs sites block generic crawlers; the flag makes the trade-off visible instead of forcing users back to wget.
- **`--user-agent` override** (matplotlib.org 403s the default UA) threads through every request including robots.txt.
- **Rate limiting stays a per-request sleep** (default 0.5s): the fetch loop is strictly sequential, so a fixed inter-request sleep *is* the rate limiter. Token bucket re-deferred per D21 until concurrent fetching exists.
- **Determinism**: crawled pages are sorted by canonical URL in `build_pack_from_url` before page/chunk-ID assignment. Discovery order (sitemap regeneration, link order) is not stable across runs; the sort satisfies the deterministic-chunk-ID requirement. llms.txt builds keep index order — that order is the site's curated priority.
- **Crawl provenance in the manifest**: crawled packs record `crawl_pages_fetched`, `crawl_truncated`, and `crawl_max_pages` (optional schema properties per D18's open-schema clause; no version bump). A truncated pack is self-describing instead of silently incomplete. Hitting `--max-pages` (default 500) is a warning, not an error — a partial pack beats no pack, and the flag + manifest keep it honest.
- **Noise filtering at the frontier**: `DEFAULT_CRAWL_NOISE_URL_PATTERNS` extends the llms noise list with generated Sphinx pages (`genindex`, `py-modindex`, `search`, `_modules`, `_sources`, `_static`, `_downloads`, `_images`) — exactly what `build-pack-html.sh` pruned manually — plus asset-extension gating pre-fetch and Content-Type gating post-fetch. `is_noise_url()` now also matches segment stems so `genindex` catches `/genindex.html` on live sites.
- **Errors**: `CrawlError(BuildError)` → exit code 6 with no CLI table changes. Root unreachable, root robots-blocked, or zero pages raise; individual page failures skip with a warning.

**Scope decision**: boto3 is excluded from the top-20 acceptance run (docs.aws.amazon.com is a ~10k-page Sphinx site spanning every AWS service). The acceptance target is 19/20 via `scripts/build_top20_packs.py`; the boto3 recipe (developer-guide subtree + raised `--max-pages`) is documented in `docs/top20-python-packages.md` instead of built by default.

**Alternatives considered**:
- **Sitemap-exclusive discovery when a sitemap exists**: fewer requests, but rejected after live validation — sitemap completeness varies wildly by generator (MkDocs: every page; ReadTheDocs: version roots only), and under-coverage is a silent correctness failure while extra requests are just polite-rate-limited time.
- **Link-following only (no sitemaps)**: simpler, but misses pages not reachable by links and ignores the coverage hint sites explicitly publish. Rejected.
- **No `--no-robots` flag**: roadmap language implied unconditional compliance, but the existing workaround script already bypassed robots for public docs; hiding the capability in wget serves nobody. Shipped with default-on compliance.
- **Failing the build on truncation**: rejected — partial pack with visible provenance beats an error after minutes of polite crawling.

**Revisit when**: a docs site needs JS rendering (out of scope for MVP-era synd), concurrent fetching becomes necessary (token bucket, per D21), or incremental recrawl lands (pages-table `etag`/`last_modified`/`fetched_at` stay deferred — a re-crawl is a full rebuild).

---

## D29: Targeted Intra-Block Splitting — Supersedes D24's Warn-Only Clause for code_block, Lists, and Paragraphs

**Decision**: `chunk_content()` splits a single top-level block that alone exceeds `max_chunk_tokens`, at structural boundaries only:

- **Indented `code_block`**: split at blank lines. These blocks are almost never real code — markdownify renders Sphinx `<dl>` API listings as 4-space-indented text, and blank lines separate the individual definitions. (Real code samples arrive as fences via `<pre>` conversion.)
- **Lists** (`bullet_list`/`ordered_list`): split between top-level list items.
- **Paragraphs**: split at line boundaries as a last resort. In practice this only fires on soft-wrapped catalog pages (one entry per line); true prose paragraphs come out of markdownify as single long lines, which have no internal line boundaries and stay whole.
- **Fences and tables remain atomic** — D24's rationale for those stands unchanged.

The between-block overflow split was also generalized from paragraph boundaries to all top-level block boundaries (fences, lists, tables, blockquotes), which is the D14 design intent extended to block types the original implementation missed.

**Why D24's warn-only stance was superseded**: D24's revisit clause required "enough data about which structural bypasses are most common to design a targeted mitigation." The v0.3.0 top-20 acceptance run (`scripts/build_top20_packs.py`, 19 real documentation sites) produced that data:

- 334 chunks exceeded 2,000 tokens across the corpus; worst cases: pytest's plugin list as one 76,088-token chunk, matplotlib's `figure_api` page as one 24,812-token chunk (a single `code_block` token spanning 1,299 lines).
- Dominant bypass classes: giant lists (release notes, API indexes — 168 of 334), catalog paragraphs (~48), `<dl>`-artifact code_blocks (~26). D24 evaluated none of these; its evidence base was two curated llms.txt packs, and the "incoherent fragments" objection targeted *arbitrary line cuts*, which this design does not make.

**Retrieval evidence (L1 eval, pilot_v1 gold set, 114 questions)**: the three pilot packs were built twice from identical live sources with old and new chunker (the only variable) and both indexes scored against the gold set:

- On the 96 questions scored on both sides, recall@1/5/10/20 were **identical to four decimals** at every cutoff; MRR/nDCG within ±0.003 (two questions moved one rank). The ~2.6% additional fragments caused no ranking dilution.
- For all 3 questions whose gold chunks the new chunker split, the gold section ranked the same or better via `search_docs` (r0069 keyword form improved rank 6 → 4).
- Chunk-size effect on the same corpus: chunks over the 1,600-token warn threshold dropped 31 → 5; worst chunk 13,400 → 6,556 tokens (remaining are atomic fences, kept per D24).

**Alternatives considered**:
- **Keeping warn-only (D24 status quo)**: rejected — the top-20 run showed the warn threshold being exceeded by 12x–47x on real crawled sites, and a 76k-token chunk is not a retrievable unit at any budget.
- **Hard cap with fence splitting**: still rejected, same rationale as D24 — a code example cut mid-function is worse than one large chunk.

**Revisit when**: an L1-style eval over a crawled-HTML gold corpus (not just curated llms.txt packs) exists to re-verify; or giant atomic fences (fastapi mkdocstrings reference pages, matplotlib's sample matplotlibrc) prove to materially degrade retrieval, which would reopen the fence-atomicity tradeoff.

**D29 revisit closed (2026-07-10)**: the crawled-HTML gold corpus now exists — `tests/evals/datasets/real/html_v1.json`, 300 questions over matplotlib/sqlalchemy/fastapi packs built with the D29 chunker and boilerplate stripping, full tier mix including the weak-half personas (direct 176, paraphrase 98, vocabulary_mismatch 26). L1 baseline (`tests/evals/results/html_l1_baseline.json`): `direct` NL recall@5 = 0.955 — the chunker/stripping changes hold up cleanly on the corpus type where they fire, no regression from the llms.txt pilot's `direct` tier (0.865 at n=42, smaller sample). The intra-block splitting and boilerplate stripping are validated; D29 stands as written.

**New finding — feeds D25's revisit trigger**: `vocabulary_mismatch` scored recall@1 through recall@20 = **0.000 at n=26** (real sample size, not the pilot's n=2). `paraphrase` recall@5 = 0.000 at n=98. Both reproduce the pilot's FTS5-ceiling finding, now at a sample size large enough to trust — a fully bare zero across all four cutoffs on 26 real vocabulary-mismatch queries against real crawled docs is not noise. D25's revisit condition ("real evidence of vocabulary-mismatch failures that tuned FTS5 cannot address") is met by this result; see `docs/hybrid-search.md` for the next step.

---

## D30: Vocabulary-Mismatch Mitigation — Stemmer First, Then Hybrid Search; D25's Revisit Clause Resolved

**Decision**: address the measured vocabulary-mismatch retrieval failure as a two-step ladder, in this order:

1. **Porter stemmer for `chunks_fts`** — change the FTS5 tokenizer from default `unicode61` to `porter` (a zero-dependency SQLite-native config change; requires an FTS5 index rebuild on schema bump, accepted in advance by D11's schema commitment note). This un-defers the "custom tokenizer" item D11 left as "low-priority for technical docs where exact terms dominate" — the html_v1 evidence revised that judgment: 4 of 26 vocabulary_mismatch questions fail purely on morphology (`formulas` vs `formula`, `savable`), and stemming also improves recall generally.
2. **Hybrid search (BM25 + vectors, RRF fusion)** per `docs/hybrid-search.md`'s existing architecture (fastembed + all-MiniLM-class ONNX at build time, sqlite-vec at query time) — sized against a **post-stemmer re-measure** of L1 on both gold corpora, so the embedding investment is justified by what the cheap fix does not recover, not by numbers that conflate the two.

**Evidence chain** (full detail in `docs/hybrid-search.md` §Evidence):

- L1 (engine ceiling): `vocabulary_mismatch` recall@1–20 = 0.000 at n=26; `paraphrase` recall@5 = 0.000 at n=98 (`html_l1_baseline.json`), reproducing the pilot corpus finding at real sample size on a second, independently generated corpus.
- L2 (Qwen3.6-27B authoring its own queries through the real search/fetch tools, `tests/evals/l2_reachability.py`): recall@5 = 0.082 / 0.143 on those tiers. The agent-compensation hypothesis — "the calling model bridges vocabulary gaps by reformulating" — is measured, real (negative reachability_gap), and **insufficient**: the model retries (avg 2.2 searches/question) but retries fail the same lexical way. 27B is the largest model in the intended sweep; the VRAM-constrained target models are expected to compensate less.
- Noise audit: zero generation artifacts in the tier; composition is 14 pure word-choice / 6 cross-ecosystem-by-design / 4 morphology / 2 typo. The tier measures what it claims.

**Failure-shape correction**: `docs/hybrid-search.md`'s original justification (0-result failures → WebFetch fallback) predates OR-semantics search and is stale. The measured failure is ranking-precision misses within non-empty results — the agent fetches plausible wrong chunks instead of falling back visibly (html_v1 r0088 observed directly in the L2 run). This is a worse failure mode and strengthens the case.

**D25 (synonym expansion) stays closed**: its revisit clause ("real evidence of vocabulary-mismatch failures that tuned FTS5 cannot address") is now satisfied, and per D25's own reasoning the answer is hybrid search, not a synonym dictionary — embeddings generalize where a hand-curated table cannot.

**Alternatives considered**:
- **Embeddings immediately, skip the stemmer** — rejected: ~23% of the measured tier (morphology + typos) is addressable with zero dependencies, and shipping the stemmer first gives the embedding decision a clean baseline instead of crediting vector search for what stemming fixed.
- **Rely on agent-side query reformulation (status quo)** — rejected by measurement: L2 recovers less than a sixth of the gap with a 27B model.
- **Synonym dictionary** — remains rejected per D25.

**Revisit when**: the post-stemmer L1 re-measure lands (decide step 2's final go/no-go and embedding model choice there); or the L2/L3 model-size sweep shows smaller models benefit disproportionately more/less from retrieval quality, which would change the priority of step 2 relative to other roadmap work.

**D30 step-1 re-measure and matrix addendum (2026-07-12)**: the porter re-measure (`*_l1_stemmer.json`) showed stemming alone is a trade, not a win: html_v1 paraphrase recall@5 went 0.000 → 0.173 (more than the 27B agent's own reformulation recovered in L2), but 9 direct-tier questions flipped 1.00 → 0.00 (stemmed tokens carry lower IDF, diluting exact matches), and **none of the audit's 4 morphology cases flipped** — their surface symptom was morphology but their failure was ranking competition, so the "≈23% recoverable by cheap lexical fixes" claim above is falsified in mechanism. The stemmer keep/revert call was **deferred** pending a joint measurement, since RRF fusion is the designed mitigation for exactly this precision tax.

That measurement now exists (`tests/evals/l1_rrf_matrix.py`, results in `*_l1_rrf_matrix.json`): a {unicode61, porter} × {BM25, RRF(BM25+MiniLM vectors)} matrix over both gold corpora, all conditions scored with the shared `retrieval_scoring` functions. Findings (NL query form):

- **RRF beats BM25-only overall on both corpora** (html recall@5 0.560 → 0.630, MRR 0.418 → 0.493; pilot recall@5 0.692 → 0.718, MRR 0.557 → 0.596) and transforms the hard tiers: html paraphrase recall@5 0.000 → 0.357–0.388, vocabulary_mismatch recall@20 0.000 → 0.418–0.495.
- **RRF has its own direct-tier recall@5 tax** (html 0.955 → ~0.85; pilot 0.865 → 0.783) while *improving* direct recall@1 — the vector list promotes some gold to rank 1 and displaces other gold from ranks 2–5. Naive unweighted RRF treats both retrievers as equals; whether weighted fusion can keep the exact-match wins is an open tuning question deliberately not explored (RRF was chosen for having no tuning).
- **Under RRF, the stemming question nearly dissolves**: rrf-porter vs rrf-unicode61 differ by ≲0.03 on most metrics (porter slightly ahead on recall@1/MRR and vocab-mismatch recall@20, slightly behind on recall@5). The vector side subsumes most of what stemming contributed. The deferred keep/revert call is therefore low-stakes if step 2 proceeds, and should be re-decided as part of the step-2 implementation rather than on BM25-only numbers.
- `vector-only` is worse than BM25 overall but is the best single retriever on vocabulary_mismatch — confirming fusion (not replacement) is the right architecture.

**Production constraint recorded (operator direction, 2026-07-12)**: hybrid must be implemented so a workflow with **no model in the loop is preserved** — the vector side is strictly additive, activating only when a pack ships embeddings and a local encoder is available; otherwise `search` degrades byte-for-byte to today's BM25-only path. Corollary surfaced by the prototype: `docs/hybrid-search.md`'s build-time-only framing left the **query-side embedding** unaddressed — chunk vectors can be precomputed, but embedding the query at search time requires a local encoder, which is exactly why graceful degradation (not a hard dependency) is the required shape.

**D30 weighted/gated fusion follow-up (2026-07-12)**: two candidate mechanisms for keeping BM25's direct-tier reliability while retaining fusion's hard-tier gains were measured (same matrix harness, conditions `rrf-w{2,3}-*` and `andgate-*`):

- **Weighted RRF (BM25 weight w : vector 1) is a smooth, effective dial.** At w=3 on html/unicode61: direct recall@5 recovers to 0.926 (vs 0.955 BM25-only, 0.852 unweighted RRF), direct recall@20 reaches **1.000** (better than BM25-only's 0.989), recall@1/MRR/nDCG all beat BM25-only — while paraphrase recall@5 stays at 0.286–0.337 (vs 0.000) and vocab-mismatch recall@20 at 0.27–0.37 (vs 0.000). The dial trades tiers monotonically: w=1 maximizes hard-tier recall, w=3 nearly restores direct precision. Cost: one tuning parameter, contra the original "RRF needs no tuning" rationale.
- **The strict-AND confidence gate underperforms on the tier it was meant to protect** (html direct recall@5 0.864): natural-language direct queries frequently fail implicit-AND too (only 241/600 html queries gated on unicode61; 300/600 on porter), so most direct queries fall through to unweighted fusion anyway. The gate does preserve unweighted fusion's hard-tier wins — andgate-porter posts the best vocab-mismatch recall@20 of any condition (0.572) — so it remains interesting as a *component* (e.g. gate → else weighted fusion), but not as the primary mechanism.
- Pilot corpus confirms the weighted pattern with a smaller residual direct tax (rrf-w3 direct recall@5 0.841 vs 0.865 BM25-only).

Working conclusion for step-2 implementation: **weighted RRF with BM25:vector ≈ 2–3:1** is the combining mechanism, with the exact weight (and any BM25-top-rank pinning, unmeasured) to be fixed during implementation review.

**D30 candidate step (2026-07-12): build-time LLM summary enrichment.** Context7's retrieval — probed directly with the two html_v1 queries that score 0.000 for synd in both L1 and L2 — returns the right answer at rank 1 for both, and a large part of the mechanism is not retrieval at all: their pipeline rewrites every snippet's description with an LLM at *index time*, so queries match generated, vocabulary-normalized prose instead of the doc author's wording (their fusion layer is otherwise the same RRF k=60 this project prototyped). The equivalent for synd is a `--summarizer llm` build option (exactly the upgrade path D3's revisit clause reserved): richer generated summaries lift both retrieval legs at once — BM25 already weights the summary column 1.5×, and the embedding input includes the summary.

- **Fits every constraint**: the cost lands at build time on the publisher (where D3's objections to LLM summaries — API keys, per-build cost, network at query time — dissolve when the generator is a *local* model), and the query-time model-free workflow is untouched.
- **Local 27B is the natural generator**: the same Qwen3.6-27B vLLM already used for the weak-half generation is well within capability for "describe this chunk in plain developer language," keeps the pipeline fully local, and costs nothing per build. Use greedy decoding (temperature 0) with a pinned model version: `normalized_content_hash` covers chunk content only (summaries excluded), so gold datasets and content verification are unaffected by summary changes — but summaries are bytes in `chunks.jsonl`, so `pack_digest` reproducibility (D5) requires deterministic generation.
- **Directly measurable with the existing harness**: regenerate summaries for the three html_v1 packs, rebuild DBs + embeddings, re-run the matrix — gold refs resolve unchanged. Run before committing to it, same as every other rung on this ladder.
- **Known risk**: hallucinated summaries pollute the index at 1.5× BM25 weight (Context7 mitigates with a query-time reranker synd cannot have) — the matrix re-measure is the check that the net effect is positive.

**D30 enrichment measure (2026-07-12)**: the 27B-summary experiment ran end to end (6,469 chunks summarized by the local Qwen3.6-27B at temperature 0, zero failures, ~70 min; `enrich_summaries.py` + `build_enriched_artifacts.py`; results in `html_l1_rrf_matrix_enriched.json`). Enrichment is the first rung on this ladder that is **not a trade** (html_v1, NL form):

- **Direct tier preserved or improved everywhere.** BM25-only with enriched summaries holds direct recall@5 at exactly 0.955 while lifting recall@1 0.473 → 0.568. Enriched rrf-w3-unicode61 reaches direct recall@5 **0.964 — above today's BM25-only 0.955** — with recall@1 0.611 and MRR 0.766 (vs 0.670). The direct-tier tax that every previous rung paid is gone.
- **Hard tiers improve on top of every retrieval strategy.** Enriched BM25-only (no vectors, no new query-time anything): paraphrase recall@5 0.000 → 0.153, beating the porter stemmer's 0.173-with-regressions at zero direct cost. Enriched rrf-porter: paraphrase recall@5 0.429, vocab-mismatch recall@10 0.462 / recall@20 0.582 (andgate-porter: 0.659). Enrichment lifts both legs as predicted — vector-only direct recall@5 rose 0.676 → 0.818 from better embedding inputs alone.
- **Sequencing consequence**: summary enrichment is a pure build-time, zero-query-time-dependency change that is a strict improvement standalone — it is the natural *first* shipping step, with weighted RRF as the second (enriched rrf-w1/w3 add the remaining hard-tier headroom). The stemmer remains a wash under both.

**D30 enrichment replication — pilot corpus (2026-07-13)**: same pipeline on the second, independently generated corpus (6,181 chunks summarized, zero failures, 2.5/s; results in `pilot_l1_rrf_matrix_enriched.json`). The headline **replicates**: every retrieval strategy improves on every overall NL metric (bm25-unicode61 MRR 0.557 → 0.593, rrf-w3 recall@5 0.679 → 0.746, recall@20 0.876 → 0.914; vector-only recall@20 0.746 → 0.818 from better embedding inputs alone), and paraphrase (n=70) improves across the board (BM25-only recall@5 0.595 → 0.639, rrf-w3 0.587 → 0.689). The vocab tier is too small on pilot (n=2) to read.

One caveat html did not show: **pilot's direct tier is not strictly preserved under BM25-only** — recall@5 0.865 → 0.827, recall@20 1.000 → 0.952 (recall@1 and MRR still improve: 0.633 → 0.657, 0.769 MRR). The 5 regressed questions are all terse/abbreviated queries (`fix api err`, `how share ctx to svc`, `why polling better than websocket`, `clear job id to rerun`, `does it need approval to go live`) — pilot's first-generation "direct" tier is noisier than html's. Mechanism: enrichment adds vocabulary mass to *every* chunk's summary, so a gold chunk that only weakly matched a terse query loses ranking ground to chunks whose new summaries happen to mention the query's words; the heuristic first-sentence summary had duplicated the gold chunk's own opening tokens, an accidental exact-match subsidy that rewriting removes. Under rrf-w3 the effect washes out (direct recall@5 0.841 → 0.828, recall@20 0.976 unchanged, recall@1 flat). Two implications for the production design (tracked in `enrichment-todo.md`): (1) consider **appending** the LLM sentence to the heuristic first-sentence summary rather than replacing it, keeping the exact-match capital — unmeasured, cheap to test with the same harness; (2) the html strict-improvement claim stands for well-formed queries, and the overall verdict — enrichment first, weighted RRF second — is unchanged by the replication.

**D30 append-vs-replace measure (2026-07-13)**: the summary-format variant proposed in the pilot-replication caveat was measured on both corpora (`*_l1_rrf_matrix_enriched_append.json`; append = heuristic first sentence + LLM sentence in the `summary` field). Verdict: **append is the production format for the BM25-only shipping step; replace is better for the embedding input.**

- **BM25-only, append ≥ replace essentially everywhere, and it repairs the terse-query caveat.** Pilot direct: recall@5 0.878 (vs 0.865 baseline / 0.827 replace), recall@1 0.681 — best of all three; recall@20 recovers to 0.976 (one of the two lost terse queries still ranks >20). html direct: recall@5 **0.972, above both baseline and replace (0.955)**, recall@20 unchanged. Hard tiers keep replace's gains (html paraphrase recall@5 0.153 either way; vocab recall@20 0.154 vs replace's 0.115). Mechanism as hypothesized: the heuristic sentence is exact-match capital, the LLM sentence is vocabulary mass — concatenation keeps both.
- **The embedding leg prefers the clean LLM sentence.** html vector-only direct recall@5: replace 0.818 vs append 0.744 (baseline 0.676) — concatenation dilutes the semantic signal, and the RRF conditions on html inherit the difference (rrf-w3 direct recall@5 replace 0.964 vs append 0.943). Pilot RRF is mixed-to-append-favoring, but the html gap is the larger and cleaner signal.
- **Production consequence** (tracked in `enrichment-todo.md`): the stored `summary` field — what BM25 indexes at 1.5× and what agents read per D12 — should be the **append** form. If/when the vector leg ships (D30 step 2), the *embedding text* should use the LLM sentence rather than the stored concatenation; the two choices are independent, and the mixed strategy (append-for-FTS + LLM-only-for-embedding) is expected to dominate both pure variants but was not separately measured.

**D30 L2 confirmation on enriched-append (2026-07-14)**: the enrichment gains survive the model-in-the-loop test, with two honest caveats (`html_l2_reachability_enriched_append.json`; same protocol as the baseline L2 — model `red`/Qwen3.6-27B authoring its own queries, 300/300 scored, ~10.5 h serial).

- **Ranked-list quality improves everywhere it was supposed to.** Overall recall@1 0.294 → 0.368, MRR 0.437 → 0.494. Paraphrase recall@5 0.143 → 0.214 (recall@1 0.041 → 0.112), vocab-mismatch recall@5 0.082 → 0.154 and recall@20 0.351 → **0.538**. The hard tiers, where the old engine gave the model literally nothing (L1 0.000), now have the agent + enriched engine finding the gold in the top-20 for half the vocab tier.
- **Caveat 1 — direct recall@5 softened under the model's own queries** (0.926 → 0.881; 12 direct questions down vs 4 up at k=5) even though the engine-side ceiling *rose* (L1 append direct recall@5 0.972, recall@1/MRR/recall@20 all up in L2 too). This is model-interaction redistribution, not index regression — more questions resolve at rank 1, a few slide from ranks 2–5 to 6–20; a single live run also carries natural variance, and the agent explored more (avg searches 1.55 → 1.75, max-turns exits 13 → 33).
- **Caveat 2 — the model's *fetch choice* barely improved**: gold-fetched rate direct 0.818 → 0.818, paraphrase 0.296 → 0.327, vocab 0.154 → 0.115 (±1 question at n=26). Retrieval now surfaces the gold far more often, but the 27B still fetches the plausible-looking wrong chunk at nearly the same rate — the residual failure has moved from the ranking layer to the model's selection layer, which build-side changes cannot reach (this is the layer Context7 attacks with a query-time reranker). Smaller sweep models are expected to select worse, not better, so the L3 endtask A/B remains the right place to see whether the improved ranked lists translate into end-task wins.

**D30 summary semantic spot-check (2026-07-14)**: the enrichment risk flagged in the candidate step (hallucinated summaries polluting the index at 1.5× BM25 weight) was audited two ways on the html corpus.

- **Automated screen, all 6,469 summaries**: word count p50=17 / max=31 (2 over the 30-word budget), zero empties, zero refusal patterns, 4 "markdown" flags that are all literal `**kwargs` syntax in valid sentences, 38 preamble-pattern flags that are all the model *correctly reporting content-free chunks* ("This excerpt provides no technical content…"). Spot-checking those 38 confirmed they are residual page furniture — prev/next nav + copyright footers and heading-only fragments that survived the boilerplate stripping (a different element pattern than the ones D29-era stripping removes; noted as a Phase-2 candidate). The model's self-labeling is desirable: it ranks junk chunks *down* instead of inventing content for them. 324 chunks share a summary with another chunk — near-duplicate API-reference fragments plus the 200 exact content-hash duplicates; expected.
- **Manual audit, 30 chunks (seed 42, 10 per pack)**: 27/30 fully accurate; **zero hallucinated capabilities, zero fabricated APIs**. 3 minor imprecisions, none inventing anything: fastapi #4614 attributes async tests to `TestClient` where the page's point is moving beyond it (the one flag with a plausible retrieval cost — it could attract "async tests TestClient" queries); matplotlib #502 describes a gallery *index* page as if it taught its two most prominent entries; fastapi #5676 calls `status_code` a response *header*. All three summaries still use vocabulary present in or adjacent to their chunks.

Verdict: generation quality is not a blocker at 27B/greedy. The failure mode to guard in production is not hallucination but the minor-attribution class — rare (≈10% of sample, all low-severity) and acceptable given the measured retrieval gains.
