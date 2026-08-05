# Phase 2.3 — Universe-Scale Ingestion Campaign and Dataset Assembly

Work in:

```text
/Users/lucatxtruong/Documents/Codex-quant
```

Continue from the current `main` branch. Read the repository first.

## Mission

Turn the current pilot-quality daily dataset into a scalable, resumable, auditable HOSE ingestion campaign.

The system should be able to ingest the full usable HOSE stock universe safely across many provider-limited batches, resume after interruption, avoid repeating completed work, assemble compatible successful runs into a coherent versioned dataset, and audit the resulting campaign as one research input.

Use your own judgment to choose the architecture, state model, CLI, storage layout, recovery strategy, merge rules, validation, tests, and documentation.

The result should make it possible to answer:

- which symbols and chunks are complete, pending, failed, stale, or incompatible;
- what can be resumed without duplicate provider work;
- which successful runs can be assembled safely;
- whether overlaps, gaps, duplicate rows, or provenance conflicts exist;
- what the final campaign-level coverage is;
- whether the assembled dataset is fit to become the canonical daily input for the next research phase.

## Non-negotiable constraints

- Do not implement features, signals, labels, strategy, backtesting, ML/HMM, portfolio logic, execution, or UI.
- Do not expose, print, log, commit, or embed `.env`, API keys, credentials, or secrets.
- Do not commit generated market data, manifests, campaign state, reports, caches, or provider responses.
- Preserve raw immutability and the provenance rules established in Phases 2.1 and 2.2.
- Do not silently merge incompatible provider, backend, contract, unit, date-range, or adjustment semantics.
- Do not fabricate point-in-time universe membership, corporate-action completeness, adjusted-price semantics, or holiday coverage.
- Keep tests offline by default.
- Live provider work must remain explicit, conservative, resumable, and rate-limit-aware.
- Failed or partial work must never masquerade as a complete assembled dataset.

The API key is available locally through the existing secure configuration path.

## Completion standard

Implement the smallest coherent system that solves campaign-scale ingestion and assembly properly.

Run the full quality gate. Perform controlled live work only as needed to validate the design. Inspect results and fix defects you find.

Save this prompt as:

```text
docs/prompts/phase-2.3-universe-ingestion-campaign-prompt.md
```

Commit and push only when checks pass and no sensitive or generated files are staged.

Return a concise completion report covering:

- architecture and decisions;
- campaign and resume model;
- assembly and compatibility rules;
- live validation scope;
- coverage results;
- tests and quality gates;
- unresolved risks;
- commit hash and push status.

Do not begin the next phase.
