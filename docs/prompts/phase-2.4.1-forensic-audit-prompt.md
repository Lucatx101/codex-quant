# Phase 2.4.1 — Forensic Audit of OHLC Failures and Stale Chunks

Work in:

```text
/Users/lucatxtruong/Documents/Codex-quant
```

Continue from the current `main` branch. Inspect the repository, campaign state, raw payloads, manifests, normalized outputs, validators, and the Phase 2.4 audit before acting.

## Mission

Determine the real causes of the unresolved campaign evidence:

- 119 failed tasks with OHLC relationship violations;
- 17 stale tasks with missing chunk edges;
- 97 failed symbols and 12 stale symbols;
- 109 symbols currently classified as not ingested.

Do not assume the provider is wrong, the validator is wrong, or the mapping is wrong. Establish the cause from evidence.

Use your own judgment to design the investigation.

The investigation should distinguish, where possible:

- bad provider payloads;
- schema or field-mapping defects;
- normalization defects;
- validator defects or invalid assumptions;
- adjusted/unadjusted price ambiguity;
- listing, delisting, suspension, transfer, or sparse-trading effects;
- chunk-boundary or truncation behavior;
- genuinely unavailable data;
- cases that remain unresolved.

Analyze representative and complete evidence as needed at row, date, symbol, task, and source-run level.

Use controlled live calls only when they materially help discriminate between hypotheses. Do not retry the unresolved campaign tasks blindly.

## Desired outcome

Produce an evidence-backed classification of every failed and stale task, with:

- root-cause category;
- supporting raw and normalized evidence;
- affected symbols and dates;
- whether the issue is deterministic;
- whether retry is justified;
- whether code must be fixed;
- whether data should be quarantined, excluded, accepted under a revised contract, or left unresolved;
- the exact next action for each category.

If the investigation reveals a code defect, fix only the defect necessary for correctness, add offline regression tests, rerun the relevant forensic checks, and update documentation.

If the evidence shows provider-side data defects or unresolved semantics, do not silently repair OHLC values, weaken validation, or mark tasks complete.

## Non-negotiable constraints

- Do not implement features, signals, labels, strategy, backtesting, ML/HMM, portfolio logic, execution, or UI.
- Do not expose or commit `.env`, API keys, credentials, raw provider data, generated market data, campaign state, receipts, manifests, forensic reports, caches, or assembled datasets.
- Preserve raw immutability and existing provenance.
- Do not overwrite or fabricate market data.
- Do not relax OHLC validation without evidence that the current rule is semantically wrong.
- Do not retry all failed or stale tasks by default.
- Unknown cases must remain explicit unknowns.

The API key is available only through the existing secure local configuration path. Never reveal its value.

## Completion standard

The phase is complete when the unresolved tasks have an auditable forensic classification and there is a defensible decision on what to retry, fix, quarantine, exclude, or leave blocked.

Run the full quality gate after any source-code change. Validate findings against the existing campaign state without starting the next phase.

Save this prompt as:

```text
docs/prompts/phase-2.4.1-forensic-audit-prompt.md
```

Commit and push only intended source, tests, and documentation changes after all checks pass. Generated forensic outputs must remain ignored.

Return a concise completion report covering:

- investigation method;
- root-cause categories and task/symbol counts;
- representative evidence;
- code defects found and fixes made;
- tasks eligible for retry;
- tasks to quarantine, exclude, or leave unresolved;
- stale-task findings;
- impact on campaign completion, assembly, and research readiness;
- tests and quality-gate results;
- unresolved external blockers;
- commit hash and push status, if code changed;
- confirmation that secrets and generated data remained uncommitted.

Do not begin the next phase.
