# Phase 2.3.1 — Separate Assembly Compatibility from Research Readiness

Work in:

```text
/Users/lucatxtruong/Documents/Codex-quant
```

Continue from the current `main` branch and inspect the repository before changing anything.

## Mission

Correct the campaign acceptance semantics introduced in Phase 2.3.

An assembled dataset being structurally compatible is not the same as being ready to serve as a canonical research input.

Separate those concepts cleanly and make the system express both independently.

Use your own judgment to decide the best architecture, naming, contracts, policy model, CLI behavior, tests, reports, and documentation.

The result should make it impossible for a campaign to become a research-ready canonical candidate merely because:

- all tasks are resolved;
- the sources are compatible;
- there are no duplicate or overlapping rows;
- unit provenance permits VND calculations.

Research readiness must also depend on explicit campaign-level coverage and quality evidence.

## Non-negotiable constraints

- Preserve the existing safe assembly rules.
- Do not weaken provenance, duplicate, overlap, atomic-publication, or partial-campaign protections.
- Do not fabricate historical membership, adjustment semantics, corporate-action completeness, or holiday coverage.
- Unknown risks must remain explicit.
- Do not implement features, signals, labels, strategy, backtesting, ML/HMM, portfolio logic, execution, or UI.
- Keep tests offline.
- Do not expose or commit secrets, `.env`, generated market data, campaign state, manifests, reports, or assembled datasets.

## Completion standard

Implement the smallest coherent correction that clearly distinguishes, at minimum:

- structural assembly compatibility;
- campaign completion;
- coverage and data-quality acceptance;
- research-readiness or canonical-candidate status.

Research-readiness criteria should be explicit, auditable, configurable where appropriate, and reflected consistently in contracts, state, reports, CLI output, manifests, tests, and documentation.

Run the full quality gate and perform local validation against the existing campaign state. Do not launch the remaining live campaign tasks in this patch.

Save this prompt as:

```text
docs/prompts/phase-2.3.1-research-readiness-policy-prompt.md
```

Commit and push only when checks pass and no sensitive or generated files are staged.

Return a concise completion report covering:

- the semantic defect;
- the chosen readiness model;
- acceptance criteria introduced;
- state, contract, report, and CLI changes;
- tests and validation results;
- unresolved risks;
- commit hash and push status.

Do not begin the next phase.
