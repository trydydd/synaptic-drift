# S5 — PyPI packaging: diagnose the blocking conflict and recommend a path

## What you are doing

Tank is a local documentation indexing tool for AI agents. It is ready to publish to PyPI but the release workflow has no publish step, and there are at least two packaging problems that must be resolved first. Your job is to diagnose both problems, evaluate the fix options, and produce a written recommendation with enough detail to implement.

Do **not** make any code changes. Read, analyse, and write up findings only.

## Repository layout (relevant files)

```
pyproject.toml                   # dependency declarations + build config
src/tank/server.py               # MCP server — imports from `mcp` (FastMCP)
src/tank/cli/main.py             # CLI entry point
.github/workflows/release.yml   # release workflow — builds wheel, no publish step
.github/workflows/ci.yml        # CI workflow
```

## Known problems to diagnose

### Problem 1 — `mcp` is a core dependency but carries a heavy server stack

`pyproject.toml` declares `mcp` under `[project.dependencies]` (not optional). `mcp` 1.27.1 requires: `anyio`, `httpx`, `httpx-sse`, `pydantic`, `pydantic-settings`, `pyjwt`, `python-multipart`, `sse-starlette`, `starlette`, `uvicorn`. That is a full ASGI server stack installed for every `pip install tank`, even for consumers who only want CLI commands (`tank query`, `tank add`, `tank sync`).

Determine:
- Exactly what `src/tank/server.py` imports from `mcp` and whether those imports appear anywhere outside `server.py` (search `src/` fully)
- Whether `tank serve` (the CLI command that starts the MCP server) can fail gracefully with a helpful error if `mcp` is not installed, rather than at import time
- What a `tank[serve]` optional extra would look like and what it would add

### Problem 2 — `chunkana>=0.1` is a core dependency but requires Python 3.12

`pyproject.toml` declares `chunkana>=0.1` under `[project.dependencies]` but the project declares `requires-python = ">=3.11"`. `chunkana` requires Python 3.12. This means `pip install tank` will fail on Python 3.11 despite the declared minimum. Independently, `chunkana` is used only by `tank build` (the pack-authoring command) — consumers who only run `tank sync` / `tank serve` / `tank query` have no use for it.

Determine:
- Which source files import from `chunkana` (search `src/` fully)
- Whether `chunkana` can be moved to the existing `build` optional extra (`tank[build]`) without breaking any import path that runs under the base install
- What the correct `requires-python` floor should be once `chunkana` is optional

### Problem 3 — no publish step in the release workflow

`release.yml` builds `dist/*.whl` and `dist/*.tar.gz` and uploads them as GitHub release artifacts, but never calls `twine upload` or the PyPI trusted-publisher API. There is no `PYPI_TOKEN` secret configured. Diagnose whether this was deferred intentionally or is just an omission, and what the minimal addition to `release.yml` would look like (PyPI trusted publisher via `pypa/gh-action-pypi-publish` is the modern approach — no token required if configured correctly).

## What to produce

A written diagnosis covering all three problems, then a recommendation choosing **one** of these paths:

**Option A — Move `mcp` and `chunkana` to optional extras, fix `requires-python`, add publish step**
- `pip install tank` → base CLI only (no MCP server, no build toolchain), works on Python 3.11+
- `pip install tank[serve]` → adds `mcp` and its stack; enables `tank serve`
- `pip install tank[build]` → adds `chunkana`; enables `tank build` (already in pyproject.toml, just needs `chunkana` moved here and `requires-python` corrected to `>=3.11`)
- Estimated scope in lines changed across `pyproject.toml`, `src/tank/cli/serve.py` (or wherever `tank serve` is wired), and `release.yml`

**Option B — CLI-only release now, server refactor deferred**
- Temporarily remove `mcp` from `[project.dependencies]` entirely; gate the `tank serve` command on a runtime import check that prints a clear install hint if `mcp` is missing
- Move `chunkana` to `tank[build]`, fix `requires-python = ">=3.11"`
- Add publish step to `release.yml`
- Document what consumers lose (no `tank serve` without explicit `pip install tank[serve]`) and when the server extra would ship
- Estimated scope

For the recommended option, include:
- The exact `pyproject.toml` diff (dependency sections only, not prose)
- The exact addition to `release.yml` for trusted-publisher PyPI publish
- Any changes needed in `src/tank/cli/` to make lazy imports or install-hint errors work correctly

## How to check your findings

```bash
# Confirm what imports mcp:
grep -rn "from mcp\|import mcp" src/

# Confirm what imports chunkana:
grep -rn "from chunkana\|import chunkana" src/

# Confirm tank serve wiring:
grep -rn "serve" src/tank/cli/main.py src/tank/cli/

# Check chunkana's Python requirement:
pip show chunkana   # or check PyPI
```

## Success criteria

- Both packaging problems are correctly diagnosed with specific file and import references
- The release workflow gap is explained
- One option is recommended with a complete `pyproject.toml` dependency diff and the `release.yml` publish step addition
- Scope estimate (lines changed) for the recommended option
- No code changes made — findings only
