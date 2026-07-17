# Synaptic Drift — Release Procedure

## Versioning Policy

Synaptic Drift follows [Semantic Versioning](https://semver.org/).

| Component | When to increment |
|---|---|
| **Major** (x.0.0) | Breaking changes to the `.ctx` pack format, the MCP tool interface, or the CLI contract |
| **Minor** (0.x.0) | New features, new CLI commands, new MCP tools, new pack fields (backward-compatible) |
| **Patch** (0.0.x) | Bug fixes, documentation corrections, dependency updates that don't change behaviour |

Pre-release identifiers: `v0.2.0-alpha.1`, `v0.2.0-rc.1`. Tags without a pre-release
identifier trigger the full release workflow.

---

## Pre-Release Checklist

Work through this list before running Cut Release.

### 1. Known bugs and gaps

Confirm the items scoped to this version in `docs/roadmap.md` are resolved or
explicitly deferred, and that no decision in `docs/decisions.md` is left open in a
way that blocks the release. (Historical note: `docs/todo.md` was folded into
`docs/roadmap.md`; there is no separate todo file.)

### 2. Tests pass cleanly

```bash
ruff check .
ruff format --check .
mypy src/
pytest tests/
```

All four must exit 0. Do not tag with failures.

### 3. Performance baseline captured

Run the token overhead benchmark and commit the result (see Performance Baselines below):

```bash
pytest tests/benchmarks/ --benchmark -v -s
cp tests/benchmarks/results/latest.json tests/benchmarks/results/v{VERSION}.json
git add tests/benchmarks/results/v{VERSION}.json
git commit -m "chore: capture v{VERSION} benchmark baseline"
```

### 4. Version bump and CHANGELOG (automated — do not do by hand)

The version bump (`pyproject.toml` `[project] version` **and** `src/synd/__init__.py`
`__version__`, kept in sync by `[tool.bumpversion]`) and the CHANGELOG roll
(`## [Unreleased]` → `## [X.Y.Z] - <date>`) are performed by the **Cut Release**
workflow. Do not edit the version strings or rename the CHANGELOG heading manually.

What you *do* need before cutting: make sure the pending work is described under
`## [Unreleased]` in `CHANGELOG.md` (Keep a Changelog format) — that block becomes
the release notes verbatim.

### 5. README reflects reality

Confirm the README quickstart works end-to-end with the current code.
The README must not say "implementation is beginning" once any release ships.

---

## Release Procedure

Releases follow the repo's `feature → develop → main` flow. **Promoting develop
to main is the release act.** There is no manual tagging and no direct commit to
`main`; three workflows chain together:

```
Actions → "Cut Release" (patch/minor/major), runs on develop
   ├─ preflight (ruff, mypy, pytest) + bump version + roll CHANGELOG
   └─ opens PR: release/vX.Y.Z → develop
        merge ↓
develop is release-ready
   └─ promote.yml auto-opens PR: develop → main   ("Release vX.Y.Z")
        review + merge ↓
auto-release.yml (fires on the version change on main)
   └─ runs full suite, builds wheel + sdist + packs,
      creates the GitHub release and tags vX.Y.Z via the API
```

Step by step:

1. **Land all release content on `develop`** via the normal feature-PR flow, and
   make sure `## [Unreleased]` in `CHANGELOG.md` describes it.
2. **Run Cut Release** (Actions → Cut Release → Run workflow → choose
   `patch` / `minor` / `major`). It always operates on `develop` regardless of the
   ref you launch it from. Review and merge the `release/vX.Y.Z → develop` PR it
   opens.
3. **Merge the promotion PR.** `promote.yml` auto-opens (or refreshes) the
   `develop → main` PR titled `Release vX.Y.Z`. Review and merge it.
4. **Auto Release runs on the merge.** Watch `.github/workflows/auto-release.yml`:
   it re-runs the full suite (including `--network`), builds the wheel/sdist and
   the fastmcp pack, and creates the GitHub release with tag `vX.Y.Z`. It aborts
   if the CHANGELOG has no section for the version (so empty notes can't ship).

> **Cutting on `develop` (not `main`) is deliberate**: the bump reaches `main` only
> through the promotion PR, so `main` stays a clean ancestor of `develop` and the
> two branches never diverge. Never PR a version bump straight into `main`.

PyPI publishing is deferred to v0.3.0. When it lands, an OIDC `publish` job is
appended to `auto-release.yml` (see the header comment there) — not a separate
tag-triggered workflow.

---

## Release Artifacts

Each GitHub Release contains the following files, attached automatically by
`.github/workflows/auto-release.yml`:

| Artifact | Description |
|---|---|
| `synaptic_drift-{VERSION}-py3-none-any.whl` | Installable wheel. `pip install synaptic-drift` or direct URL install. |
| `synaptic_drift-{VERSION}.tar.gz` | Source distribution. Required for downstream repackaging (Debian, Homebrew, etc.). |
| `fastmcp@{VERSION}.ctx` | Pre-built documentation pack for FastMCP, built from `llms-full.txt` in CI. |

### Adding packs to a release

The `Build packs` step in `auto-release.yml` builds one pack per library listed in
the workflow. To add a library:

1. Confirm the library publishes `llms-full.txt`.
2. Add a `curl` + `synd build` line to the `Build packs` step in `auto-release.yml`.
3. The `.ctx` file is picked up by the `files:` glob automatically. (The pack step
   is best-effort — a fetch failure degrades the release rather than blocking it.)

### Artifact naming convention

`.ctx` packs are named `{name}@{version}.ctx` where `version` is the *library*
version, not the Synaptic Drift version. Example: `fastmcp@3.3.0.ctx` built by Synaptic Drift v0.2.0.

---

## Performance Baselines

Token overhead benchmarks are pinned to each release to track regressions and
improvements over time. Results are committed to `tests/benchmarks/results/`.

### Running the benchmark

```bash
pytest tests/benchmarks/ --benchmark -v -s
```

Output is printed to stdout and written to `tests/benchmarks/results/latest.json`.

### What is measured

| Metric | Description |
|---|---|
| Schema tokens | Token cost of `query-docs` + `resolve-deps` tool definitions |
| Schema % of context | Schema tokens as a fraction of 200K and 128K context windows |
| Summary response, N=5/10/20 | Tokens returned by `query-docs` at `detail="summary"` |
| Full response, N=5/10/20 | Tokens returned by `query-docs` at `detail="full"` |
| Progressive disclosure saving | `(naive_full_n20 − two_step_total) / naive_full_n20` |

The two-step pattern (step 1: summary scan → step 2: targeted full fetch of top 3)
is Synaptic Drift's primary token efficiency claim. The saving % is the headline number.

### Token counter

Benchmarks use `len(str) // 4` — the same approximation used throughout Synaptic Drift's
codebase. This is ±15% accurate for English prose. For exact cl100k counts,
install `tiktoken` and replace `_count_tokens` in `tests/benchmarks/test_token_overhead.py`.
If you switch counters between releases, note it in the benchmark result's
`token_counter` field.

### Interpreting deltas

Compare `v{N}.json` against `v{N-1}.json` before tagging. Expected behaviour:

| Change | Schema tokens | Summary response | Full response | Progressive saving |
|---|---|---|---|---|
| Add a new MCP tool | Increases | Unchanged | Unchanged | Unchanged |
| Add a field to `_to_dict` | Unchanged | Increases | Increases | May decrease |
| Improve FTS ranking (fewer irrelevant results) | Unchanged | Decreases | Decreases | Increases |
| Increase default `limit` in `search()` | Unchanged | Increases | Increases | Varies |

**Regression thresholds** (guidelines, not hard rules):

- Schema tokens increase by >20% → investigate before shipping; a new tool should
  be justified in the changelog.
- Progressive disclosure saving drops below 40% → review whether `_to_dict` has
  grown or whether FTS result quality has degraded.
- Summary tokens/result increase by >15% → a field has been added to the summary
  response; confirm this was intentional.

### Result file format

```json
{
  "timestamp": "2026-05-20T12:00:00+00:00",
  "git_commit": "abc1234",
  "synd_version": "0.1.0",
  "token_counter": "len_div_4",
  "corpus": {
    "chunks": 20,
    "avg_summary_chars": 112,
    "avg_content_chars": 1640
  },
  "schema": {
    "total_tokens": 245,
    "tools": [
      {"name": "query-docs", "tokens": 170, "chars": 680},
      {"name": "resolve-deps", "tokens": 75, "chars": 301}
    ],
    "pct_of_200k_context": 0.122,
    "pct_of_128k_context": 0.191
  },
  "responses": {
    "summary_n5":  {"tokens": ..., "actual_results": 5,  "tokens_per_result": ...},
    "summary_n10": {"tokens": ..., "actual_results": 10, "tokens_per_result": ...},
    "summary_n20": {"tokens": ..., "actual_results": 20, "tokens_per_result": ...},
    "full_n5":     {"tokens": ..., "actual_results": 5,  "tokens_per_result": ...},
    "full_n10":    {"tokens": ..., "actual_results": 10, "tokens_per_result": ...},
    "full_n20":    {"tokens": ..., "actual_results": 20, "tokens_per_result": ...}
  },
  "progressive_disclosure": {
    "step1_summary_all_tokens": ...,
    "step2_full_top3_tokens": ...,
    "total_tokens": ...,
    "vs_naive_full_n20_tokens": ...,
    "saving_pct": 58.3
  }
}
```

---

## Post-Release Steps

1. **Verify PyPI**: `pip install synaptic-drift=={VERSION}` in a clean virtualenv. Run
   `synd --version` and confirm it prints the correct version.

2. **Verify GitHub Release**: Check that all expected artifacts are attached —
   wheel, sdist, and all `.ctx` packs.

3. **Update README badge**: If the README has a PyPI version badge, it updates
   automatically. Confirm it shows the new version within ~5 minutes.

4. **Announce** (when applicable): Post to the project's discussion forum,
   Discord, or mailing list. Link the GitHub Release, not the tag.

5. **Open milestone for next version**: Create a GitHub milestone for
   v{NEXT} and move any deferred issues into it.

---

## Hotfix Procedure

For an urgent patch on an already-shipped version, when `develop` contains
unreleased work you don't want to ship yet:

1. Branch from the tag: `git checkout -b hotfix/vX.Y.Z vX.Y.(Z-1)`.
2. Make the fix and add a `## [Unreleased]` CHANGELOG entry; run `pytest tests/`.
3. Open a PR from the hotfix branch **into `main`**, bumping the patch version and
   rolling the CHANGELOG in that same PR (mirror what Cut Release does: bump
   `pyproject.toml` + `src/synd/__init__.py`, and turn `## [Unreleased]` into
   `## [X.Y.Z] - <date>`). Merging it triggers `auto-release.yml` and ships the
   patch.
4. **Back-merge to `develop`** afterward (`git checkout develop && git merge main`,
   via PR) so `main` remains an ancestor of `develop` and the fix isn't lost.

This is the one case where a change lands on `main` ahead of `develop`; the
mandatory back-merge in step 4 repairs it. Do not run the performance benchmark
for hotfixes unless the fix touches search, the MCP server, or response
serialisation.
