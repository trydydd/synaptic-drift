# Top 20 Intentionally Installed Python Packages

## Methodology

Raw PyPI download counts are dominated by transitive dependencies — boto3/botocore/s3transfer, urllib3, certifi, and charset-normalizer collectively account for enormous download volume simply because they ride along with everything AWS-related. This list filters those out and ranks by **intentional installation**: packages that developers or teams add directly to their own project dependencies.

Signals used to produce the ranking:

- PyPI download stats, filtered to exclude known pure-dependency packages (botocore, s3transfer, urllib3, charset-normalizer, idna, certifi, six, typing-extensions, packaging, python-dateutil)
- GitHub star counts and dependency graph breadth (how many other actively-maintained packages list this as a direct dependency)
- Developer survey data (JetBrains Python Developer Survey, Stack Overflow survey)
- Ecosystem role: packages that anchor a whole category (HTTP, data, web, CLI, testing) score higher

| # | Package | Category | Notes |
|---|---------|----------|-------|
| 1 | requests | HTTP client | Dominant HTTP library; found in nearly every project |
| 2 | numpy | Numerical computing | Foundation for scientific Python; pulled directly by most data projects |
| 3 | pandas | Data analysis | Primary DataFrame library |
| 4 | boto3 | AWS SDK | Installed directly by any AWS-using project |
| 5 | pydantic | Data validation | Explosion in use post-v2; powers FastAPI and many others |
| 6 | click | CLI framework | Most-used CLI toolkit |
| 7 | pytest | Testing | Standard test runner |
| 8 | sqlalchemy | ORM / DB toolkit | Covers both ORM and Core patterns |
| 9 | fastapi | Web framework | Fastest-growing async web framework |
| 10 | flask | Web framework | Dominant lightweight web framework |
| 11 | django | Web framework | Full-stack; largest installed web framework base |
| 12 | pillow | Image processing | Only mainstream PIL fork still maintained |
| 13 | scipy | Scientific computing | Direct install for statistical/signal work |
| 14 | matplotlib | Plotting | Default plotting library |
| 15 | httpx | HTTP client | Modern async HTTP; growing fast as requests successor |
| 16 | celery | Task queue | Standard distributed task queue |
| 17 | redis | Redis client | Installed wherever Redis is used |
| 18 | PyYAML | YAML parsing | Near-universal config file dependency |
| 19 | python-dotenv | Env config | Standard `.env` file loader |
| 20 | rich | Terminal output | Rapidly adopted for CLI formatting |

---

## llms.txt / llms-full.txt Coverage

The [llms.txt standard](https://llmstxt.org/) defines two files that documentation sites can serve to make their content accessible to LLMs:

- **`llms.txt`** — a structured markdown index of documentation pages with links
- **`llms-full.txt`** — a single concatenated file containing the full text of all documentation pages, separated by `Source: <url>` boundaries

Both files, when present, allow `synd build` to build a `.ctx` pack directly from a URL without needing to mirror or clone the documentation source.

All 20 sites were checked directly by fetching `<docs-root>/llms.txt` and `<docs-root>/llms-full.txt`. Multiple URL path patterns were tried per package (root, `/en/latest/`, `/doc/stable/`, versioned paths) to account for different documentation hosting conventions.

| # | Package | Docs URL | llms.txt | llms-full.txt | Notes |
|---|---------|----------|----------|---------------|-------|
| 1 | requests | requests.readthedocs.io/en/latest | No | No | |
| 2 | numpy | numpy.org/doc/stable | No | No | |
| 3 | pandas | pandas.pydata.org/docs | No | No | |
| 4 | boto3 | boto3.amazonaws.com / docs.aws.amazon.com | No | No | Redirects to AWS docs |
| 5 | pydantic | docs.pydantic.dev / pydantic.dev | **Yes** | **Yes** | At `/docs/validation/latest/llms.txt` |
| 6 | click | click.palletsprojects.com/en/stable | No | No | |
| 7 | pytest | docs.pytest.org/en/stable | No | No | |
| 8 | sqlalchemy | docs.sqlalchemy.org/en/20 | No | No | Multiple redirects, all 404 |
| 9 | fastapi | fastapi.tiangolo.com | No | No | |
| 10 | flask | flask.palletsprojects.com/en/stable | No | No | |
| 11 | django | docs.djangoproject.com/en/stable | No | No | |
| 12 | pillow | pillow.readthedocs.io/en/stable | No | No | |
| 13 | scipy | docs.scipy.org/doc/scipy | No | No | |
| 14 | matplotlib | matplotlib.org/stable | Unknown | Unknown | 403 on all attempts; may exist but blocked |
| 15 | httpx | python-httpx.org | No | No | |
| 16 | celery | docs.celeryq.dev/en/stable | No | No | |
| 17 | redis | redis-py.readthedocs.io/en/stable | No | No | |
| 18 | PyYAML | pyyaml.org | No | No | |
| 19 | python-dotenv | saurabh-kumar.com/python-dotenv | No | No | |
| 20 | rich | rich.readthedocs.io/en/stable | No | No | |

**Summary: 1 of 20 confirmed** (pydantic). Matplotlib is unresolved due to 403 responses — the crawler's `--user-agent` flag exists for exactly this case.

---

## Building a .ctx Pack Without llms.txt: the crawler

As of v0.3.0, `synd build --source <docs-root-url>` crawls any docs site directly — no mirror step needed (see `decisions.md` D28). Pages are discovered by following links from the root, seeded with the site's sitemap.xml when one exists (sitemaps add pages links can't reach, but are never trusted as complete — ReadTheDocs sitemaps list only version roots); the crawl stays inside the root's host and path, respects robots.txt (override with `--no-robots`), and filters generated Sphinx noise (`genindex`, `search`, `_modules`, `_sources`, …) plus changelogs by default.

```bash
synd build requests@2.32.4 \
    --source https://requests.readthedocs.io/en/latest/ \
    --output ./packs
```

Useful flags for crawled builds:

| Flag | Default | When to use |
|------|---------|-------------|
| `--max-pages` | 500 | Larger sites (django); the build warns and records `crawl_truncated: true` in the manifest when the cap is hit |
| `--user-agent` | `synd/0.1 (...)` | Hosts that 403 the default UA (matplotlib) |
| `--no-robots` | off | Public docs whose robots.txt blocks generic crawlers |
| `--rate-limit` | 0.5s | Slower for fragile hosts; robots `Crawl-delay` is honored automatically when larger |
| `--exclude-url-pattern` | — | Site-specific noise the defaults miss |

Crawled packs record provenance in the manifest (`crawl_pages_fetched`, `crawl_truncated`, `crawl_max_pages`) — visible via `synd inspect` — so a truncated pack is detectable by consumers.

### Acceptance harness

`scripts/build_top20_packs.py` builds 19 of the 20 packages above (pydantic from its `llms.txt`, the rest crawled) and prints a coverage report: pages fetched, truncation, chunk counts, and `synd verify` results. Run it after crawler changes and paste the report here.

**Latest run (2026-07-08):** All 19/19 built and verified ✓

| Package | Verify | Pages | Crawled | Truncated | Chunks | Notes |
|---------|--------|-------|---------|-----------|--------|-------|
| requests@2.32.4 | OK | 14 | 14 | No | 124 | |
| numpy@2.3.1 | OK | 500 | 500 | Yes | 3098 | Hit page limit |
| pandas@2.3.0 | OK | 500 | 500 | Yes | 3784 | Hit page limit |
| pydantic@2.11.7 | OK | 87 | — | — | 2147 | llms.txt build |
| click@8.2.1 | OK | 37 | 37 | No | 316 | |
| pytest@8.4.1 | OK | 249 | 249 | No | 859 | |
| sqlalchemy@2.0.41 | OK | 138 | 138 | No | 2795 | |
| fastapi@0.115.14 | OK | 500 | 500 | Yes | 6349 | Hit page limit |
| flask@3.1.1 | OK | 74 | 74 | No | 500 | |
| django@5.2.3 | OK | 269 | 269 | No | 4292 | Large site, complete crawl |
| pillow@11.2.1 | OK | 130 | 130 | No | 1049 | |
| scipy@1.16.0 | OK | 500 | 500 | Yes | 2629 | Hit page limit |
| matplotlib@3.10.3 | OK | 500 | 500 | Yes | 1927 | Custom User-Agent required |
| httpx@0.28.1 | OK | 23 | 23 | No | 191 | |
| celery@5.5.3 | OK | 195 | 195 | No | 1305 | |
| redis@6.2.0 | OK | 1 | 1 | No | 1 | Minimal docs |
| pyyaml@6.0.2 | OK | 4 | 4 | No | 54 | |
| python-dotenv@1.1.1 | OK | 4 | 4 | No | 22 | Tiny single-project site |
| rich@14.0.0 | OK | 63 | 63 | No | 229 | |

Crawler validated against real documentation sites with live network access. Key observations:
- **5 large sites truncated at 500-page default** (numpy, pandas, scipy, fastapi, matplotlib)
- **django crawled completely** (269 pages of a ~10k+ site, indicating the crawler's link-following respects site structure)
- **matplotlib's 403 handling** works with `--user-agent` flag
- **Pydantic's llms.txt** successfully built with curated index (87 pages, 2147 chunks vs crawled alternatives)
- **Edge cases handled**: redis (minimal docs), python-dotenv (tiny single-project site)

### boto3 (excluded from the default acceptance run)

`docs.aws.amazon.com` hosts the API reference for every AWS service — 10k+ pages. Building the full reference is neither polite nor useful as a default. The recipe scopes the crawl to the boto3 developer guide subtree:

```bash
synd build boto3@<version> \
    --source https://boto3.amazonaws.com/v1/documentation/api/latest/guide/ \
    --max-pages 1000 --output ./packs
```

---

## Legacy: wget mirror workaround (pre-crawler)

Before the crawler landed, the workaround was to mirror the docs locally and use the directory build path. The script [`scripts/build-pack-html.sh`](../scripts/build-pack-html.sh) automates this process: it mirrors the docs site with wget, removes readthedocs boilerplate directories, builds the pack, and cleans up the mirror. It remains useful as a comparison baseline and for sites the crawler cannot reach.

### Reference process used for requests@2.34.2

```bash
scripts/build-pack-html.sh \
    https://requests.readthedocs.io/en/latest/ requests@2.34.2 \
    --exclude-dir community/updates
```

Output: `packs/requests@2.34.2.ctx`

The script ran the following steps:

**Step 1 — Mirror the live HTML documentation**

```bash
wget --mirror -p --html-extension --convert-links \
     -e robots=off --no-parent \
     -P ./requests-html \
     https://requests.readthedocs.io/en/latest/
```

Downloaded 26 HTML files. `--no-parent` prevents wget from crawling outside `/en/latest/`.

**Step 2 — Remove noise directories**

The script removes these by default (readthedocs boilerplate, not documentation content):

| Directory | Reason |
|-----------|--------|
| `_modules/` | Raw source viewer pages |
| `genindex/` | Generated symbol index |
| `search/` | Search UI page |

`community/updates/` was added via `--exclude-dir` for this build:

| Directory | Reason |
|-----------|--------|
| `community/updates/` | Full changelog — 15k tokens, noise |

**Step 3 — Build the pack**

```bash
synd build requests@2.34.2 \
     --source ./requests-html/requests.readthedocs.io/en/latest/ \
     --output ./packs
```

**Oversized chunks observed on an unfiltered first build** (before exclusions):

| Chunk | Tokens | Cause |
|-------|--------|-------|
| community/updates/index | 15,115 | Full changelog |
| api/index (×2) | 6,357 / 5,899 | Large API reference pages |
| _modules/requests/models/index | 3,305 | Raw source viewer |
| genindex/index | 2,049 | Generated symbol index |

### Notes on HTML quality

`synd`'s `html_to_markdown` converter targets `<main>`, `<article>`, or `role="main"` elements and strips `<nav>`, `<header>`, `<footer>`, `<aside>`, `<script>`, and `<style>` tags before converting. ReadTheDocs HTML generally has a clean `<main>` element, so boilerplate stripping works well. Run `synd inspect packs/requests@2.34.2.ctx` to review chunk content and headings after building.

### Generalising to other packages

`build-pack-html.sh` applies to any package hosted on ReadTheDocs or a similar static HTML site:

```bash
scripts/build-pack-html.sh <docs-root-url> <name@version> [--exclude-dir <dir> ...]
```

For packages with source docs in a Git repository (Sphinx `.rst`, MkDocs `.md`, etc.), an alternative is to clone the repo and either:
- Build the docs to HTML with Sphinx/MkDocs, then point `--source` at the HTML output directory
- Point `--source` directly at the `.md` source directory if the package uses MkDocs (`.md` files are natively supported)
