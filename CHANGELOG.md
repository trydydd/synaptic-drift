# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-07-17

**Theme**: Growth — a general web crawler for docs sites without an `llms.txt`,
plus retrieval and release-tooling improvements.

### Added
- `synd build --source <url>` — general web crawler for documentation sites that publish neither `llms.txt` nor `llms-full.txt`. BFS link-following seeded by sitemaps (robots `Sitemap:` directives → `<root>/sitemap.xml` → `<host>/sitemap.xml`, with `sitemapindex` recursion), host + path-prefix scoping, canonical-URL dedup, and a per-host `robots.txt` cache honoring `Crawl-delay` (with a `--no-robots` escape hatch). Configurable `--user-agent` and a `--max-pages` cap; crawl provenance (`crawl_pages_fetched` / `crawl_truncated` / `crawl_max_pages`) recorded in the manifest. Static HTML only — no JS rendering or embeddings. Crawled pages are sorted by canonical URL before chunk-ID assignment for deterministic builds. (D28)
- `extract_links()` and `fetch_html()` fetch primitives (Content-Type + redirect aware) in `synd.builder.fetch`.
- Evaluation harness under `tests/evals/` — L1/L2/L3 retrieval and end-task measurement with two gold corpora. Internal tooling; no runtime or packaging impact.

### Changed
- Search: replaced AND-matching-with-relaxation by **OR-join + BM25 ranking**, fixing keyword-form regressions where relaxation dropped the wrong terms (D29).
- Release flow reworked around the `develop → main` branching model. `cut-release.yml` now runs on `develop`: it bumps the version, rolls `CHANGELOG` `[Unreleased]` into a dated version section, and opens a PR **into develop**. `promote.yml` (new) auto-opens the `develop → main` promotion PR when a version bump lands on develop; merging it triggers `auto-release.yml`, the single tag-and-release engine. `main` stays a clean ancestor of `develop`.
- `bump-my-version` now also rewrites `src/synd/__init__.py` `__version__`, which previously went stale on every automated bump.
- `auto-release.yml` fails loudly if the CHANGELOG has no section for the version being released, and treats the fastmcp pack build as best-effort so a docs-site outage can't block a release.

### Removed
- `release.yml` — dead on the automated path (a `GITHUB_TOKEN`-created tag does not fire `on: push: tags`) and a duplicate of `auto-release.yml`. `auto-release.yml` is now the only release engine.
- `scripts/release.sh` — a divergent third release path (pushed tags directly to `main`, referenced a nonexistent `.venv312`). Superseded by the Cut Release → promote → Auto Release flow.

## [0.2.0] - 2026-07-16

**Theme**: effortless start — new command verbs, URL-based builds, a rebuilt
chunker, and a machine-checkable pack contract.

### Added
- `synd serve` — launches the MCP stdio server, discoverable from `synd --help`; replaces the undiscoverable `python -m synd.server` invocation.
- `synd sync` — reads `synd.lock`, skips already-imported packs (idempotent), runs a digest pre-check against the lockfile before the 8-step verifier (supply-chain guard), and imports any missing packs. Enables `git clone && synd sync` on a fresh checkout. `--frozen` blocks any pack that would require network access.
- `synd remove <pkg@ver>` — removes a pack from `index.db` and rewrites `synd.lock`, completing the verb set (previously required hand-editing the lockfile).
- `synd build --source <url>` for `llms-full.txt` and `llms.txt` sources — fetches a documentation URL, preprocesses it (MDX/JSX stripping, per-page splitting), then chunks and builds a `.ctx` pack. Basic rate limiting and `User-Agent`. (S6/S8, D21)
- URL noise filtering for URL builds — excludes changelog/release/news pages via segment-level path matching; `--exclude-url-pattern` (repeatable) and `--no-url-filter`.
- Chunk-size controls on `synd build`: `--max-chunk-tokens` (default 800), `--min-chunk-tokens` (default 20), and `--warn-chunk-tokens` (D24); plus `scripts/validate_chunk_sizes.py` for real-data validation.
- `schemas/manifest.v2.schema.json` — machine-readable JSON Schema as the single source of truth for manifest fields; the verifier validates against it.
- Schema-validated MCP tool-response contract and a differentiated CLI exit-code taxonomy — `outputSchema` on the MCP tools and a dedicated exit code per error class (D27).
- `SearchError` raised on all invalid / all-stopword FTS5 queries instead of silently returning `[]` (S12, D27).
- FTS5 ranking: `heading_path` added as the first `chunks_fts` column, weighted 2.5×; BM25 weights tuned (heading 2.5 > summary 1.5 > content 1.0). Query sanitization strips special characters (no more crashes on `mcp.tool`-style queries); stopword filtering / term normalization in `_preprocess_query()`.
- `synd.lock` written by `synd add` and committed to version control (analogous to `Cargo.lock`); `src/synd/cli/_lockfile.py` provides shared `read_lockfile()` / `write_lockfile()` used by `add`, `sync`, and `remove`. Lockfile `source_url` prefers the manifest's canonical HTTPS URL over the local import path.
- `LockfileError`, `FetchError`, `PackNotFoundError` exception classes in `synd.errors`.
- Query-latency benchmark against a 100K-chunk real documentation index (`tests/benchmarks/test_query_latency.py`, `docs/search-benchmarks.md`).
- `auto-release.yml` — GitHub-release automation triggered by a version bump on `main` (runs the full check+build pipeline and creates the release via the API).
- Decision-log entries D13–D27 and research spikes S5–S12 documenting the above.

### Changed
- **MCP interface (breaking):** the single `query-docs` tool (with a `detail` parameter) is replaced by two tools — `search` (summaries + chunk IDs) and `fetch` (full content by ID) — structurally enforcing the two-step retrieval pattern.
- **CLI (breaking):** `synd pull` renamed to `synd add`, consistent with `cargo add` / `uv add` / `npm install <pkg>` ("pull" wrongly implied a remote fetch; the command only imports local files).
- Chunker: a custom `markdown-it-py` chunker replaces chunkana — splits at all heading levels (`#`–`######`), keeps code fences atomic, and builds `heading_path` accurately by construction, removing the `##`-only limitation that produced multi-section chunks (D14).
- Summaries: heading-aware heuristic prefixes each chunk summary with its leaf heading (D13).
- MDX/Mintlify handling: pipeline-order and `<Tab>` de-indentation fixes that previously produced 15k–22k-token "monster" chunks; `<Tab title=…>` is injected as a heading with a depth shift so language tabs get distinct `heading_path` values (D21, D22).
- Cross-platform path handling: paths normalized to forward slashes; backslashes/UNC rejected in the validator.
- `pack_digest` computation hashes each ZIP entry's content directly in filename-sorted order, eliminating the full in-memory ZIP reconstruction (previously allocated 500MB+ near the archive limit).
- Packaging: build dependencies moved into the core install, with `[all]`/`[dev]` extras for contributors; `python -m synd` guarded against a missing `serve` extra.
- Error messages audited so every error path emits an actionable message.
- `cut-release.yml` repurposed to push a `release/vX.Y.Z` branch and open a PR instead of pushing a tag directly, fixing a latent bug where a `GITHUB_TOKEN`-authenticated tag push failed to trigger the release workflow (GitHub loop-prevention).
- Docs: `benchmarks.md` renamed to `search-benchmarks.md`, with numbers updated to the 2500-rep baseline.
- Project-wide rename: remaining `tank` references swept to `synd` / Synaptic Drift.

### Fixed
- Local-directory builds convert HTML to markdown before chunking (previously chunked raw HTML).
- Minimum-token merge suppresses heading-only stub chunks inside the chunker (~249 stubs eliminated from the MCP pack without a post-processing pass) (D23).
- Deduplicate `Source:` URLs in `split_llms_full_txt`.

### Removed
- `synd pull` command and `pull.py` — the brief deprecated alias for `synd add` is gone; use `synd add`.

## [0.1.1] - 2026-05-23

### Added
- CI workflow (lint, typecheck, test) on all pushes and pull requests
- Release workflow: runs full test suite (including network tests), builds wheel/sdist, fetches docs source, and publishes a GitHub release with all artifacts
- `cut-release.yml` — manually triggered GitHub Actions workflow (Actions → Cut Release → Run workflow); accepts `patch / minor / major`, runs pre-flight checks, bumps version, commits, and pushes the tag to trigger the Release workflow
- Network integration test for the full FastMCP build + query pipeline
- `bump-my-version` in dev dependencies; `[tool.bumpversion]` config in `pyproject.toml`
- `scripts/release.sh` — local equivalent of Cut Release for dev machines with a 3.12 venv

### Fixed
- `pack_digest` verification: ZIP entry timestamps are now pinned to a fixed epoch (`2021-08-08`) so the archive is reproducible across machines and the digest can be independently verified
- Ruff lint errors (unused imports and variables)
- MyPy errors in builder module

### Changed
- ZIP epoch documented in architecture docs and `CLAUDE.md`
- `ruff` pinned to `0.15.8` to prevent formatting drift across CI runs
- Packs removed from the repository; built and published as release artifacts instead

## [0.1.0] - 2025-01-01

### Added
- Initial MVP: `synd build`, `synd query`, `synd inspect`, `synd verify`, `synd pull`
- SQLite FTS5 search backend with WAL mode
- `.ctx` pack format: deterministic ZIP archive with `manifest.json` and per-chunk files
- `pack_digest` integrity field in manifest
- MCP server (`synd.server`) exposing `query-docs` and `resolve-library-id` tools
- Policy engine for source and chunk filtering
- HTML → text conversion via basic tag removal
- Heuristic chunk summarisation (first sentence / leading signature)
