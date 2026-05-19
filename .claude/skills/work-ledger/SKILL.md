---
name: work-ledger
description: >
  Creates and maintains machine-first work ledgers for the Tank project.
  Use this skill when you need to plan implementation work for execution by
  another model (especially local/smaller models like Nvidia Spark, Qwen, or Sonnet),
  when the user mentions a "work ledger", "task ledger", or "machine-first plan",
  when handing off coding work between models, or when decomposing the Tank
  architecture into structured implementation chunks with definitions of done.
---

# Work Ledger Skill

A work ledger is a monolithic YAML file that lets a large planner model (Opus) decompose
Tank implementation work into chunks that an executor model can implement safely and
independently, then hand back to Opus for review. The ledger is the sole source of
truth — no planning conversation context is assumed on the executor's side.

## Bundled assets

- `assets/work-ledger.template.yaml` — Tank-specific ledger template with inline docs
- `assets/planning-prompt.md` — The prompt Opus uses to fill in the ledger

Always read both files before starting any ledger work.

## Source of truth

The codebase is always the source of truth — not documentation, not the ledger from a
prior session. Before writing or revising a ledger:

1. Read `docs/architecture.md` for the target state (what we're building toward)
2. Inspect the actual codebase (`src/tank/`, `tests/`) for current state (what exists now)
3. The delta between target and current is the work

Never assume something is implemented (or not) because a document says so. Check the files.

## Workflow

### 1. Identify the mode

**Creating a new ledger** — User has a plan (or the architecture docs) and no existing
ledger. Read the codebase to determine current state before writing chunks.

**Revising an existing ledger** — User provides an existing ledger. Do not touch
DONE or IN_PROGRESS chunks. Revise PENDING chunks freely. Add new chunks as needed.
Record structural changes in `# PLANNER NOTE:` YAML comments.

**Answering a ledger question** — User asks about the schema, a specific field, or
how to handle an edge case. Answer directly from this skill and the template.

### 2. Gather what you need

Before writing any YAML, confirm you have:

| Required | Field it feeds | Tank source |
|---|---|---|
| What the software does and who uses it | `global.project_summary` | `docs/architecture.md` Goals + MVP Definition |
| Language, runtime, test runner, key libraries + versions | `global.tech_stack` | `.claude/CLAUDE.md` + `docs/architecture.md` Technology Choices |
| Architecture decisions already made | `global.architecture_decisions` | `docs/decisions.md` + `.claude/CLAUDE.md` |
| File/export naming conventions | `global.repo_conventions` | `.claude/CLAUDE.md` Code Style |
| Executor model name | `meta.models.executor` | Ask the user |
| Executor profile (`constrained` / `standard` / `trusted`) | `meta.executor_profile` | Ask the user |
| Current implementation state | All chunks | **Read the codebase** — `src/tank/`, `tests/` |

Most global fields can be populated directly from Tank's existing docs. The critical
input that requires live inspection is the current codebase state.

If anything is missing, ask once. Do not write placeholder chunks — gaps in the
plan surface as gaps in `interface_contract`, which cause executor failures.

If the plan is ambiguous but asking would be disruptive, resolve conservatively:
smaller scope, safer assumption. Record the resolution in the affected chunk's
`assumptions` field with a `# RESOLVED AMBIGUITY:` prefix.

### 3. Write the ledger

Read `assets/work-ledger.template.yaml` for the full schema.
Read `assets/planning-prompt.md` for planning rules and the self-check.

Key rules to apply:

**Executor profile** — set `meta.executor_profile` first; it governs four fields:

| Profile | `assumptions` | `capability_ceiling` | `outputs.exports` | `local_context` |
|---|---|---|---|---|
| `constrained` | Exhaustive | Tight, with examples | Full types | Full orientation |
| `standard` | Non-obvious only | Architectural decisions | Names + return types | What exists + why |
| `trusted` | Gotchas only | True unknowns only | Names only | Brief pointer |

Fields unaffected by profile — keep rigorous regardless: `behaviors`, `taboos`,
`definition_of_done`, `review_targets`, `verification_inputs`, `negative_tests`,
`interface_contract.inputs`, `interface_contract.allowed_new_deps`, progress log,
`rollback_anchor`.

**Chunks**
- One cohesive unit of work per chunk. When in doubt, split.
- Size hints: `small` <30k tokens of work, `medium` 30–100k, `large` >100k (rare).
- Tank dependency order: normalizer → storage/models → builder → validator → policy → search → CLI → MCP server → integration tests.
- `depends_on` must be acyclic.

**Interface contracts**
- `inputs`: every file/symbol the chunk reads that it did not create.
- `outputs`: every file/symbol created, with full type signatures — not just names.
- `allowed_new_deps`: empty list means no new packages permitted.

**Assumptions**
- Every piece of tacit knowledge you used to design the chunk goes here.
- For Tank specifically: normalizer determinism requirements, hash computation order,
  archive format details, SQLite FTS5 behaviour, chunkana API specifics.
- If an assumption being wrong would cause the executor to produce incorrect code,
  it must be listed.

**Capability ceiling**
- Frame each entry as: "If you encounter X, do not decide — stop and flag it."
- For Tank: dependency choices, schema changes, normalization rule changes (hash stability),
  MCP tool surface changes, .ctx format changes.

**Review targets**
- Written at planning time while failure modes are fresh.
- Must be checkable yes/no. Not "code is clean" — "normalize() preserves content
  inside fenced code blocks verbatim — verified in tests."
- Minimum two per chunk.
- Each target includes `verified_by` (test function name) and `assertion` (concrete
  expected behavior). This enables end-of-project batch review without re-deriving
  intent from test code.

**Verification inputs**
- Golden-pair test data (input → expected output) for functions on the integrity path:
  normalization, hashing, sort ordering, policy evaluation.
- The executor must implement these as actual test assertions — not just documentation.
- Include edge cases for the failure modes anticipated during planning: whitespace-only
  lines, empty inputs, sign flips, partial configs with missing keys.
- Each case has a `label` so the reviewer can verify coverage without reading the function.

**Negative tests**
- 1–3 tests per chunk verifying that wrong behavior does NOT occur.
- These catch "works but subtly wrong" bugs: worst-first sort ordering, empty-list
  defaults that block everything, skipped production code paths.
- **Inlining rule (v2.3)**: every negative test name MUST appear in
  `interface_contract.outputs` alongside the regular test functions, with a
  `# NEG: <description>` inline comment. The executor implements what it sees
  in `interface_contract.outputs` — if a test isn't listed there, it won't be written.
- The `negative_tests` section still exists as structured reviewer reference
  (with `must_not` conditions), but the test names are duplicated into
  `interface_contract.outputs` to ensure executor compliance.
- Focus on bugs that pass all other tests but fail in production or under composition.

**Writing style for assumptions and checklists**
- Promote critical qualifiers out of parentheticals — small models miss nested clauses.
- Distinguish behavioral requirements ("operation is atomic") from implementation
  requirements ("code must contain literal BEGIN/COMMIT SQL"). If you mean specific
  code, write the code in the assumptions section.
- Specify default-fallback for every optional config key independently, not just the
  "no file found" case. Partial files merge with defaults, not override completely.
- When a value is transformed before sorting, state the sort direction with the
  transformed value. Do not leave the executor to re-derive sort order after sign flips.
- For testability parameters with `None` default, specify that None means "use production
  default," not "skip this step."
- For ranked-output functions, require a multi-result ordering test — single-result
  tests cannot catch ordering bugs.

**Progress log**
- Do not pre-populate `progress.txt`. Leave it empty except for the standard header.
- The executor creates it if absent and appends; it never overwrites.
- Standard header:

```
# progress.txt
# Auto-maintained by executor. Do not edit manually.
# Gotcha bar: a competent developer would waste >15 min cold AND
#             it is absent/wrong in official docs.
# Format: [chunk-id] YYYY-MM-DD / GOTCHA / REPRO / WORKAROUND / AFFECTS
# ─────────────────────────────────────────────────────────────
```

**Executor briefing**
Include the following verbatim as a comment block at the top of every ledger,
before the `meta:` key:

```yaml
# =============================================================================
# EXECUTOR BRIEFING — READ THIS ENTIRE FILE BEFORE TOUCHING ANY CODE
# =============================================================================
#
# You are the EXECUTOR. You implement; you do not plan or architect.
#
# BEFORE EACH CHUNK:
#   1. Read this entire ledger file from top to bottom.
#   2. Read progress.txt in full.
#   3. Locate your assigned chunk by id and begin.
#   4. Confirm all inputs exist. If any are missing, raise an open_question immediately.
#   5. Read prerequisites.read_first files in order.
#
# DURING EACH CHUNK:
#   6. Write tests first (TDD). Tests must fail before implementation.
#   7. Implement ALL test functions listed in interface_contract.outputs —
#      including tests marked with "# NEG:" comments (negative tests).
#   8. For each verification_inputs case, your test assertions must check
#      the EXACT expected output, not a weaker property.
#   9. Do not make decisions listed in capability_ceiling — stop and flag them.
#  10. Do not add dependencies not in interface_contract.allowed_new_deps.
#
# BEFORE SUBMITTING (transitioning to NEEDS_REVIEW):
#  11. Run every command in definition_of_done.automated. All must exit 0.
#  12. Append to progress.txt using the gotcha entry format.
#      Write "[chunk-id] No gotchas." if none — never skip this step.
#  13. Fill in handoff: status_note, files_modified, open_questions,
#      progress_log_updated: true.
#  14. Set chunk status to NEEDS_REVIEW.
#
# DO NOT:
#   - Process more than one chunk at a time.
#   - Start a chunk before all depends_on chunks are DONE.
#   - Modify DONE chunks.
#   - Invent missing inputs. Stop and ask.
# =============================================================================
```

### 4. Self-check before outputting

For each chunk, confirm:

1. Could the executor start this chunk with only the ledger and the repo?
2. Is `local_context` depth appropriate for the executor profile?
3. Are all `inputs` things that actually exist (or will after `depends_on` chunks)?
4. Are `assumptions` scoped correctly — not too thin, not padded with noise?
5. Do `review_targets` encode the failure modes anticipated during design?
6. Is `capability_ceiling` calibrated to the profile — not blocking good judgment, not permitting architectural drift?
7. Is `progress.txt` the first entry in every chunk's `prerequisites.read_first`?

Fix any "no" before outputting.

### 5. Output format

Output the complete ledger as a single YAML code block. No prose before or after it.
If revising, output the full file — not a diff.

Write the ledger to `.work/ledger.yaml`.

## Chunk status state machine

```
PENDING → NEEDS_REVIEW → VERIFIED → DONE
```

- **PENDING**: Not started.
- **NEEDS_REVIEW**: Executor finished, awaiting verification pipeline.
- **VERIFIED**: Automated pipeline (review-chunk.sh) passed all completion promise lines.
- **DONE**: Reviewer (Opus) signed off after independent review.

Opus transitions VERIFIED → DONE after independent review, or back to NEEDS_REVIEW
with a `rejection_reason` that is specific and actionable, referencing `review_targets`.

## Late-project context management

Once many chunks are DONE, their `handoff` and `review` blocks accumulate.
If context pressure appears, strip the content of those blocks from DONE chunks
and archive separately. Keep the chunk header and `interface_contract` — the executor
needs to know what exists, even for completed chunks.

## Gotcha entry format (reference)

```
[chunk-id] YYYY-MM-DD
GOTCHA: <one-line searchable description>
REPRO:  <when and how it bites you>
WORKAROUND: <what actually works>
AFFECTS: <which future chunks or areas should read this>
```
