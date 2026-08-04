# Phase 2.1 — Enforce Liquidity Unit Provenance

## Repository

```text
/Users/lucatxtruong/Documents/Codex-quant
```

GitHub:

```text
https://github.com/lucatx101/codex-quant.git
```

Python package:

```text
hose_quant
```

The repository is public.

Use `python3` in user-facing commands.

---

## Mission

Patch Phase 2 so that monetary liquidity calculations can never be treated as verified merely because a user selects a CLI option.

The current Phase 2 implementation is otherwise accepted.

The specific defect is:

- `--unit-policy verified-kbs-ohlcv` can currently promote generic normalized daily data to verified KBS units without machine-checkable source provenance;
- this allows `average_traded_value_vnd` to be computed even when the normalized input does not prove that its price and volume units match the KBS contract.

This phase must convert unit verification from a user assertion into an evidence-backed invariant.

Do not begin Phase 3.

---

## Autonomous Engineering Mandate

Inspect the current Phase 2 implementation before changing anything.

Review at least:

- normalized daily schema and provenance fields;
- Phase 1 normalizers;
- feature-input unit policies;
- CLI construction;
- data contracts;
- validators;
- storage/manifests;
- current tests and documentation.

Choose the smallest durable fix that fits the repository architecture.

You may refactor the current unit-policy design if that produces a cleaner and more general provenance model.

Do not add a second, parallel provenance system.

---

## Hard Invariant

A monetary unit policy may be marked `verified` only when the input dataset itself contains sufficient machine-checkable provenance to prove that the policy applies.

A CLI flag, environment setting, config value, filename, symbol list, or user assertion is not sufficient evidence by itself.

Unknown provenance must remain unknown.

If source provenance is missing, ambiguous, or incompatible:

- do not compute VND traded value;
- do not expose a verified monetary status;
- fail explicitly when a VND threshold is requested;
- preserve non-monetary liquidity metrics where valid.

---

## Required Outcome

The implementation must establish a clear chain:

```text
provider/source metadata
→ normalized dataset provenance
→ verified unit interpretation
→ monetary liquidity calculation
```

The system must make it impossible for a generic Phase 1 normalized daily dataset to become `verified-kbs-ohlcv` solely through CLI selection.

The exact design is yours to determine.

Reasonable approaches include:

- validating a source identifier stored in normalized daily rows;
- adding versioned unit-provenance metadata to normalized outputs;
- deriving unit policy from validated dataset metadata rather than accepting it as a free-form user choice;
- separating `user_asserted` from `verified` status;
- rejecting legacy datasets that lack sufficient provenance.

Prefer the design that will extend cleanly to future providers.

---

## Backward Compatibility

Legacy Phase 1 normalized daily files may not contain source-specific unit provenance.

Handle them honestly.

Acceptable behavior:

- keep them usable for OHLCV panels and non-monetary liquidity metrics;
- mark unit status as unverified or legacy-unverified;
- reject VND thresholds and traded-value calculations;
- explain the remediation path.

Do not silently rewrite legacy files as verified.

Do not make live API calls merely to repair local provenance.

---

## CLI Behavior

Review the existing `--unit-policy` interface.

Retain, rename, restrict, or remove it according to the chosen architecture.

Whatever interface remains must not permit verified status without matching input provenance.

CLI help and errors should explain:

- what evidence is required;
- why a dataset was rejected for monetary calculations;
- how legacy data is treated.

Do not require API credentials for Phase 2.1 tests or local transformations.

---

## Data Contract and Manifest Requirements

Unit provenance should be visible in the resulting contracts and manifests.

The implementation should expose, as appropriate:

- provider;
- source/data backend;
- provenance status;
- unit policy name/version;
- verification status;
- evidence or evidence reference;
- reason when verification is unavailable;
- whether VND traded value is permitted.

Use typed or versioned representations consistent with the existing codebase.

Do not represent documentation text alone as machine verification.

---

## Tests

Add or revise offline tests around the invariant.

At minimum, prove that:

1. Generic normalized daily data cannot obtain verified KBS units through CLI selection alone.
2. Legacy data remains usable for non-monetary liquidity metrics.
3. A VND threshold on unverified data fails explicitly.
4. Verified monetary calculation succeeds only when the fixture contains matching machine-checkable provenance.
5. Incompatible provenance is rejected.
6. Manifest/output metadata reflects the effective verification status.
7. No API key or network access is required.
8. Existing Phase 2 behavior outside this patch remains intact.

Prefer invariant-focused tests over implementation-specific tests.

---

## Documentation

Update the existing Phase 2 documentation so it matches the actual implementation.

Explain:

- how unit verification works;
- which provenance is required;
- how legacy Phase 1 files behave;
- when monetary liquidity is unavailable;
- how a future normalized dataset can become eligible for verified VND calculations.

Update README only where needed.

Save this prompt as:

```text
docs/prompts/phase-2.1-unit-provenance-prompt.md
```

---

## Validation

Run the complete repository quality gate:

```bash
make check
```

Also inspect:

```bash
git status --short
git diff --check
git diff --stat
git diff
git check-ignore .env
git check-ignore data/feature_inputs/vnstock/test.parquet
git check-ignore reports/feature_inputs/test.json
```

Run representative local smoke tests only if suitable local data already exists.

Do not make fresh live provider calls for this patch.

---

## Acceptance Criteria

Phase 2.1 is complete only when:

1. Verified monetary liquidity cannot be enabled by CLI assertion alone.
2. Verification requires matching machine-checkable dataset provenance.
3. Legacy or ambiguous data remains explicitly unverified.
4. Unverified data cannot produce `average_traded_value_vnd`.
5. VND liquidity thresholds fail clearly on unverified data.
6. Non-monetary liquidity characterization still works on legacy data.
7. Contracts, manifests, CLI help, and docs reflect the effective provenance state.
8. Offline tests cover verified, unverified, legacy, and incompatible cases.
9. `make check` passes.
10. No secrets or generated market data are committed.
11. No strategy, signal, label, backtest, ML, portfolio, execution, or UI code is introduced.

---

## Git Completion

Before committing, inspect unstaged and staged changes carefully.

Commit and push only if all checks pass and the worktree contains no unintended generated or sensitive files.

Use an appropriate conventional commit message, for example:

```text
fix: enforce liquidity unit provenance
```

Push to:

```text
origin main
```

Do not force-push.

---

## Completion Report

Return:

1. Root cause.
2. Architecture chosen.
3. Provenance evidence now required.
4. Legacy-data behavior.
5. CLI behavior changes.
6. Contract and manifest changes.
7. Tests added or changed.
8. Quality-gate result.
9. Local smoke-test result, if run.
10. Files changed.
11. Commit hash and push status.
12. Confirmation that secrets and generated data remained uncommitted.

Do not begin Phase 3.
