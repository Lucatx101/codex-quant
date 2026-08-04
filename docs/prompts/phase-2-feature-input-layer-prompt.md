# Phase 2 — Feature Input Layer

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

Design and implement the next trustworthy data layer for this quantitative trading system.

Phase 0 and Phase 1 are complete. The existing system can already:

- fetch and normalize the current HOSE universe;
- backfill daily OHLCV;
- fetch limited intraday bars;
- capture batch quote snapshots;
- write manifests;
- validate stored datasets;
- keep generated market data outside Git.

Your task is to transform the existing normalized data into reliable, research-ready inputs for future feature engineering and backtesting.

This phase should establish strong data contracts and expose uncertainty rather than hiding it.

The intended outcomes are:

- a defensible tradable-universe representation;
- a stable daily market-data panel;
- transparent data-availability and quality diagnostics;
- explicit market-time and timestamp conventions;
- parameterized liquidity screening;
- clear provenance and uncertainty metadata;
- concise technical documentation for future research phases.

Do not implement alpha or trading logic.

---

## Autonomous Engineering Mandate

Begin by inspecting the repository thoroughly.

Understand the existing:

- architecture;
- storage conventions;
- normalized schemas;
- validation framework;
- manifest system;
- CLI style;
- configuration model;
- tests;
- documentation;
- Git-ignore rules.

Then choose the best architecture for the repository as it actually exists.

You have authority to:

- reuse, extend, consolidate, or refactor existing abstractions;
- choose module boundaries;
- choose data-contract representations;
- choose CLI/API structure;
- improve weak Phase 1 abstractions when this materially improves correctness;
- add validation or diagnostics not explicitly listed here;
- reject or revise assumptions that are technically unsafe;
- deviate from anticipated implementation details when a better design exists.

Do not create a parallel architecture merely to match this prompt.

Do not treat possible names, paths, columns, commands, or algorithms as fixed unless they are explicitly listed under **Hard Invariants** or **Acceptance Criteria**.

Prefer a small number of durable abstractions over a large checklist of narrowly scoped modules.

Do not ask for approval on routine implementation choices. Make a reasoned decision and document it.

Stop only when:

- required information is genuinely unavailable;
- an action would be destructive;
- two plausible designs would create materially different public contracts that cannot safely be resolved from the repository.

---

## Hard Invariants

These are non-negotiable.

### Scope boundary

Do not implement:

- buy or sell signals;
- alpha features;
- future-return labels;
- strategy rules;
- backtesting;
- portfolio construction;
- position sizing;
- ML or HMM models;
- broker integration;
- live execution;
- web UI.

This phase prepares trusted inputs only.

### No look-ahead

Every transformation must preserve causal data availability.

Do not use future observations to classify, filter, fill, or modify past records.

Any rolling statistic must be backward-looking and must have explicit window semantics.

### No false point-in-time claims

The available universe is currently a provider snapshot, not a verified historical point-in-time membership dataset.

Do not allow a current universe snapshot to masquerade as a historical universe merely because a user supplies an arbitrary `as-of` date.

Universe provenance must distinguish at least:

- when the source snapshot was observed or fetched;
- any requested research reference date;
- whether historical membership is verified.

If true historical membership is unavailable, expose that limitation structurally and in documentation.

### No unsupported instrument classification

Do not label an instrument as common stock, actively listed, or tradable unless source data supports that conclusion.

Use conservative candidate classifications and explicit reason/provenance fields.

Unknown must remain unknown.

### No silent timezone assumptions

Do not silently localize timezone-naive provider timestamps.

Timestamp handling must preserve:

- original timestamp value;
- timezone-awareness status;
- source/provider context;
- any interpretation or localization assumption.

`Asia/Ho_Chi_Minh` is the target market timezone convention, but it must not be asserted as verified provider semantics without evidence.

### No unsupported price-adjustment assumptions

Adjusted versus unadjusted price semantics remain unresolved.

Do not represent price adjustment status as known unless verified from provider behavior or documentation.

Daily panel outputs must carry explicit adjustment uncertainty.

Do not construct adjusted prices from incomplete corporate-action data.

### Verify units before liquidity calculations

Before deriving traded value or applying monetary liquidity thresholds, inspect and verify the units of normalized price and volume.

Do not assume that:

```text
close × volume
```

is denominated in VND without proving the price scale.

The resulting unit and scaling policy must be explicit, tested, and documented.

If units cannot be established reliably, expose the metric as unavailable or uncertain rather than producing misleading values.

### Preserve missingness

Do not forward-fill OHLCV data.

Do not synthesize market bars merely to create a rectangular panel.

Missing observations must remain observable through the data contract and diagnostics.

### Data and secret hygiene

Never expose or commit:

- `VNSTOCK_API_KEY`;
- `.env`;
- credentials;
- raw provider payloads;
- generated market datasets;
- generated manifests;
- generated reports;
- local caches or logs.

Use the existing ignored-data conventions unless there is a strong reason to improve them.

Tests must be offline by default.

---

## Required Capabilities

The implementation must provide the following capabilities. Their internal design and external interface are yours to determine.

### 1. Universe preparation

Create a reproducible process that converts the normalized provider universe into a research-usable universe representation.

It should:

- normalize and validate symbols;
- constrain by exchange;
- handle duplicates and malformed records;
- preserve useful source classification fields;
- distinguish included candidates from excluded or uncertain records;
- retain filter reasons;
- retain snapshot provenance;
- avoid false historical-universe semantics;
- support later extension to better instrument classification.

Rows must not disappear without an auditable reason.

### 2. Liquidity characterization and screening

Provide a parameterized method for characterizing recent liquidity from daily data.

The design should support meaningful measures such as:

- trading frequency;
- zero-volume frequency;
- rolling average volume;
- rolling traded value when units are verified;
- recent valid close;
- insufficient-history status;
- missing-data status.

The exact metrics and interfaces should follow evidence from the available data and the needs of future short-horizon HOSE research.

Screening thresholds must be configurable.

Screening must produce diagnostics rather than silently discarding instruments.

### 3. Daily research panel

Build a stable, typed daily panel from normalized OHLCV data.

The panel must:

- preserve symbol and date identity;
- use deterministic ordering;
- reject or clearly report duplicate keys;
- enforce OHLCV validity;
- preserve missing observations;
- retain source provenance;
- expose adjustment status;
- avoid future-looking transformations;
- be suitable as the canonical input to later feature engineering.

Choose long-form, wide-form, partitioning, schema enforcement, and storage details based on the repository architecture and expected research use.

Do not compute alpha features or target labels.

### 4. Availability and quality diagnostics

Provide per-symbol diagnostics sufficient to judge whether a dataset is suitable for research.

At minimum, the system should make it possible to inspect:

- observed date range;
- observation count;
- duplicate count;
- missing or invalid OHLC records;
- zero-volume observations;
- expected-versus-observed weekday coverage;
- absence of data;
- known limitations of the expected-session calculation.

A weekday-based expectation is acceptable temporarily, but Vietnamese public holidays must not be silently treated as missing trading days without documenting the limitation.

### 5. Market-time convention

Establish a clear market-time policy for:

- daily dates;
- intraday timestamps;
- timezone-aware timestamps;
- timezone-naive timestamps;
- HOSE regular trading sessions;
- lunch break;
- weekdays;
- incomplete holiday knowledge.

The implementation should be useful now without pretending to be a complete exchange calendar.

### 6. Feature-input contract

Define the contract that future feature and backtesting code can rely on.

The contract should make clear:

- required fields;
- key uniqueness;
- ordering;
- data types;
- nullability;
- provenance;
- adjustment status;
- timestamp status;
- liquidity-unit status;
- missing-data semantics;
- universe-membership limitations.

Use the representation most appropriate for the existing codebase: typed models, schema objects, validation functions, metadata fields, documentation, or a combination.

---

## Implementation Quality

The implementation should feel native to the existing repository.

Prefer:

- clear domain boundaries;
- typed interfaces;
- deterministic transformations;
- pure functions where useful;
- explicit metadata;
- composable validation;
- actionable error messages;
- testable I/O boundaries;
- minimal duplication;
- backward compatibility where reasonable.

Avoid:

- speculative frameworks;
- unnecessary abstractions;
- one-off code paths;
- duplicated storage logic;
- hardcoded dates or symbols;
- hardcoded liquidity thresholds;
- hidden provider assumptions;
- broad exception swallowing;
- large dependency additions without clear value.

You may add or change dependencies when justified. Report the reason for each material dependency change.

---

## CLI and User Experience

Expose the new capabilities through interfaces consistent with the existing project.

The exact command names and options are not prescribed.

A user should be able to:

- prepare a cleaned HOSE universe from locally stored normalized data;
- optionally apply configurable liquidity criteria;
- build a daily panel for selected symbols and date ranges;
- generate availability and quality diagnostics;
- inspect resulting manifests and reports.

Prefer discoverable CLI help and coherent option naming over reproducing any previously suggested command verbatim.

Do not require live API calls for these transformations.

Use the existing manifest mechanism for material generated operations unless repository inspection reveals a better unified design.

---

## Testing Strategy

Add rigorous offline tests using synthetic or sanitized fixtures.

Design tests around system invariants, not file names.

Coverage should include, where relevant:

- malformed and missing symbols;
- duplicate universe records;
- unsupported classification fields;
- universe snapshot provenance;
- prevention of arbitrary historical backdating;
- insufficient liquidity history;
- verified and unverified price units;
- configurable liquidity thresholds;
- missing daily observations;
- duplicate symbol-date keys;
- invalid OHLC relationships;
- zero-volume observations;
- no forward filling;
- adjustment-status uncertainty;
- timezone-aware timestamps;
- timezone-naive timestamp provenance;
- expected weekday coverage;
- CLI integration;
- manifest creation;
- generated-output ignore behavior.

Add additional tests when repository inspection reveals important failure modes.

Tests must not depend on vnstock availability, network access, API keys, or current market data.

---

## Documentation

Save this implementation brief in:

```text
docs/prompts/phase-2-feature-input-layer-prompt.md
```

Create or update concise technical documentation describing:

- the chosen architecture;
- public commands or APIs;
- output contracts;
- universe semantics;
- liquidity methodology and units;
- missing-data policy;
- timestamp and timezone policy;
- adjustment uncertainty;
- corporate-action limitations;
- point-in-time limitations;
- generated-output locations;
- examples using local data;
- known unresolved issues.

Update the README only where needed for discoverability and usage.

Do not generate large reports for inclusion in Git.

---

## Validation and Smoke Testing

Run the repository’s complete quality gate.

At minimum:

```bash
make check
```

Also verify:

```bash
git status --short
git diff --check
git check-ignore .env
git check-ignore data/raw/vnstock/test
git check-ignore data/normalized/vnstock/test
git check-ignore data/manifests/test.json
```

Inspect staged and untracked files carefully.

If suitable local normalized data already exists, run representative local smoke tests.

Do not make fresh live API calls merely to satisfy this phase.

Smoke-test failures caused by defects must be fixed.

Smoke-test limitations caused by missing local data should be reported honestly.

---

## Acceptance Criteria

Phase 2 is complete only when:

1. A coherent feature-input layer has been implemented using the repository’s existing architecture or a clearly superior refactor.
2. Universe preparation is reproducible and auditable.
3. Current universe snapshots cannot silently masquerade as historical point-in-time membership.
4. Liquidity calculations cannot silently use unverified monetary units.
5. Daily panel construction preserves missingness and key integrity.
6. Timestamp provenance and timezone assumptions are explicit.
7. Price-adjustment uncertainty is explicit.
8. Data availability and quality can be evaluated per symbol.
9. Material operations integrate with project validation and provenance mechanisms.
10. Offline tests cover the important invariants and failure modes.
11. `make check` passes.
12. No live API access is required by tests.
13. No secret or generated market data is staged or committed.
14. Technical documentation reflects the actual implementation.
15. No strategy, signal, label, backtest, ML, portfolio, execution, or UI code is introduced.

If a requested capability cannot be implemented honestly from the available data, represent it as unavailable or uncertain and explain why. Do not fabricate completeness.

---

## Git Completion

Before committing, inspect:

```bash
git status --short
git diff --stat
git diff
```

Stage only intended source code, tests, documentation, and configuration changes.

Then inspect:

```bash
git diff --cached --stat
git diff --cached --name-only
git diff --cached
```

Commit and push only if all checks pass and no generated or sensitive files are staged.

Use an appropriate conventional commit message, for example:

```text
feat: build feature input layer
```

Push to:

```text
origin main
```

Do not force-push.

---

## Completion Report

Return a concise but technically complete report containing:

1. Architecture chosen and why.
2. Important assumptions challenged or changed.
3. Files and modules changed.
4. Public CLI/API capabilities added.
5. Data contracts introduced.
6. Validation and diagnostics added.
7. Tests added and failure modes covered.
8. Quality-gate results.
9. Local smoke-test results, if run.
10. Dependency changes and justification.
11. Known limitations and unresolved data risks.
12. Git commit hash and push status.
13. Confirmation that secrets and generated data remained uncommitted.

Do not begin Phase 3.
