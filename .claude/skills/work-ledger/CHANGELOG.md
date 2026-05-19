# Work Ledger Skill — Changelog

## v2.4 — 2026-05-19

### Status workflow simplified

**Old**: `PENDING → IN_PROGRESS → NEEDS_REVIEW → APPROVED` (with `REJECTED` branch)

**New**: `PENDING → NEEDS_REVIEW → VERIFIED → DONE`

- **PENDING**: Not started.
- **NEEDS_REVIEW**: Executor finished, awaiting verification pipeline.
- **VERIFIED**: Automated pipeline (review-chunk.sh) passed all completion promise lines.
- **DONE**: Reviewer (Opus) signed off after independent review.

**Rationale**: `APPROVED` was ambiguous (plan approved? code approved?). `DONE` is unambiguous. `IN_PROGRESS` was unused in practice — the executor runs in a fresh context and sets `NEEDS_REVIEW` on completion. `VERIFIED` was added to distinguish "automated pipeline passed" from "reviewer signed off." `REJECTED` is replaced by transitioning back to `NEEDS_REVIEW` with a `rejection_reason`.

### Migration

- Update `ledger_version` to `2.4`
- Rename all `status: APPROVED` to `status: DONE`
- Rename all `outcome: "APPROVED"` to `outcome: "DONE"`
- Update executor briefing DO NOT block: `DONE` replaces `APPROVED`

---

## v2.3 — 2026-05-18

**Context**: Chunk 07 review (first treatment-group chunk) revealed that the executor ignored all three `negative_tests` entries and did not use `verification_inputs` to calibrate test assertions. The v2.2 fields correctly targeted the bug category (source_url missing source dir name), but the executor never read them — it implemented only what was listed in `interface_contract.outputs`. Bug rate: 1 bug in 1 chunk, same category as control group (spec detail missed during implementation).

### Structural change: negative tests inlined into interface_contract

**Problem**: The executor reads `interface_contract.outputs` as its test implementation list. The `negative_tests` section is a separate YAML key that the executor treated as reviewer-only documentation. All three negative tests specified for chunk 07 went unimplemented.

**Fix**: Every negative test name now appears in BOTH places:
1. In `interface_contract.outputs` — with a `# NEG: <description>` inline comment, so the executor implements it alongside regular tests
2. In `negative_tests` — with the full `must_not` condition, for reviewer verification

This is a placement fix, not a content fix. The same information exists; it's now where the executor actually looks.

### Updated executor briefing

Steps 7-8 now explicitly mention negative tests and verification_inputs:
- Step 7: "Implement ALL test functions listed in interface_contract.outputs — including tests marked with `# NEG:` comments"
- Step 8: "For each verification_inputs case, your test assertions must check the EXACT expected output, not a weaker property"

### New artifact: review prompt template

Added `.work/review-prompt-template.md` — a mechanical verification prompt for use with `claude -p` in a fresh context window. This enables a free "pass 2" verification loop between executor and reviewer:
1. Pass 1 (executor, local model): implement the chunk
2. Pass 2 (verifier, local model via `claude -p`): fresh context, read ledger spec, execute verification_inputs, check test coverage, report pass/fail with evidence
3. Reviewer (Opus): verify completion promise, catch anything the checklist missed

The template is parameterized by `{{CHUNK_ID}}` and designed to be invoked as:
```bash
claude -p "$(cat .work/review-prompt-template.md | sed 's/{{CHUNK_ID}}/chunk-08-validator/g')" --model <local-model> --max-turns 25
```

### Rationale

The v2.2 experiment showed that adding new ledger sections doesn't help if the executor doesn't read them. Instead of adding more sections, v2.3 puts the critical information where the executor already looks (`interface_contract.outputs`) and adds a separate verification pass to catch what the executor still misses. The cost structure makes this viable: local model passes are free, so adding a pass 2 is purely a wall-clock cost.

### Migration

- Update `ledger_version` to `2.3`
- For PENDING chunks: add negative test names to `interface_contract.outputs` with `# NEG:` comments
- Keep `negative_tests` section as-is (reviewer reference)
- No changes needed for DONE chunks

---

## v2.2 — 2026-05-18

**Context**: Review of Tank chunks 01–06 (executor: qwen3.6-35b-a3b-fp8-dflash, reviewer: claude-opus-4-6) found 5 bugs across 4 chunks. All bugs passed the executor's own tests and automated DOD checks. Root cause analysis revealed recurring patterns traceable to ledger design, not executor incompetence. These changes restructure the ledger to prevent those bug classes.

### New chunk fields

**`verification_inputs`** — Golden-pair test data for integrity-path functions.

- Provides concrete input/expected-output pairs the executor must implement as test assertions.
- Targets functions where "close but wrong" is hard to detect: normalization, hashing, sort ordering, policy evaluation.
- Each case has a `label` so the reviewer can verify coverage without reading the function body.

*Motivated by*: Chunk 03 — normalizer regex `\n{3,}` missed whitespace-only blank lines. The ledger described the behavior in prose but provided no concrete test case. A golden pair like `"para1\n   \n   \npara2" → "para1\n\npara2"` would have forced the correct regex.

**`negative_tests`** — Tests verifying wrong behavior does NOT occur.

- 1–3 per chunk, each becomes an actual test function the executor writes.
- Targets subtle correctness traps: wrong sort order, empty-list defaults, skipped code paths.

*Motivated by*: Three bugs that "worked" but were wrong:
- Chunk 05 — `dict.get("allowed_lifecycle_states", [])` silently blocked all packs when the key was missing from a partial policy file. A negative test like `test_partial_policy_does_not_block_all` would have caught this.
- Chunk 05 — `if home_dir is not None` skipped the `~/.tank/policy.toml` lookup in production. A negative test like `test_home_dir_none_does_not_skip_user_policy` would have caught this.
- Chunk 06 — `ORDER BY score ASC` on negated BM25 scores returned worst match first. A negative test like `test_search_does_not_return_worst_first` would have caught this.

### Updated chunk fields

**`review_targets`** — Now assertion-level instead of prose.

- Each target includes `verified_by` (test function name) and `assertion` (concrete expected behavior).
- Enables end-of-project batch review: the reviewer checks assertions against the test suite directly, without re-deriving what each test was supposed to verify.

*Motivated by*: Per-chunk frontier review is expensive. Assertion-level targets make batch review viable by giving the reviewer a checklist that maps directly to test code.

### New planning rules

**Writing style for assumptions and checklists** — Six rules addressing recurring executor failure patterns:

1. **Promote qualifiers out of parentheticals.** Small models parse top-level statements more reliably than nested clauses.
   - *From*: Chunk 03 — key detail "(possibly with whitespace-only lines between them)" was buried in a parenthetical and missed.

2. **Distinguish behavioral from implementation requirements.** When the checklist says "uses a transaction," state whether it means atomicity (behavioral) or literal BEGIN/COMMIT SQL (implementation).
   - *From*: Chunk 04 — executor used Python sqlite3 implicit transactions (behaviorally atomic) instead of explicit BEGIN/COMMIT (the ledger's actual requirement).

3. **Specify default-fallback for every optional config key independently.** State what happens when each key is missing, not just when the whole file is missing.
   - *From*: Chunk 05 — "default policy allows X" was stated for `Policy.default()` but not for `_parse_policy()`. Executor used `dict.get(key, [])` instead of falling back to the permissive defaults.

4. **State sort direction with transformed values.** When a value is negated/transformed before sorting, give the complete pattern — don't leave the executor to re-derive sort order.
   - *From*: Chunk 06 — executor negated BM25 scores (correct) but kept ASC sort (incorrect). The ledger gave two options without connecting them.

5. **Specify production defaults for testability parameters.** When a parameter like `home_dir: Path | None = None` is added for testing, state that None means "use the real default," not "skip."
   - *From*: Chunk 05 — `home_dir=None` guard condition was inverted, skipping the user-level policy lookup in production.

6. **Require multi-result ordering tests for ranked output.** Single-result tests cannot catch ordering bugs.
   - *From*: Chunk 06 — existing test had only one search result, making the ASC/DESC bug invisible.

### Rationale

The overarching goal is to shift bug detection from reviewer time (expensive frontier tokens) to executor time (cheap local tokens). Each new field gives the planner a way to encode anticipated failure modes as executable test specs rather than prose descriptions. This makes the executor's test suite a more reliable proxy for correctness, reducing the reviewer's job from "find bugs" to "verify the specified checks were implemented."

The writing-style rules address a different failure class: specs that are technically correct but structured in ways that small models misparse. These are free — they cost nothing at execution time and require only awareness during planning.

### Migration

Existing ledgers (v2.1) continue to work. The new fields are additive:
- `verification_inputs` and `negative_tests` default to empty if omitted.
- `review_targets` accepts both the old string format and the new structured format.

To adopt: update `ledger_version` to `2.2` and backfill the new fields for PENDING chunks. Do not modify DONE or in-progress chunks.
