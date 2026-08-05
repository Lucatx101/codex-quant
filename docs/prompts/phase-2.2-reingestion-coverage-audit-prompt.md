# Phase 2.2 — Provenance-Aware Re-ingestion and Coverage Audit

Work in:

```text
/Users/lucatxtruong/Documents/Codex-quant
```

The repository is public. Read the repository first and continue from the current `main` branch.

## Mission

Make the local market-data foundation genuinely ready for the next research phase.

Use the provenance-aware ingestion path introduced in Phase 2.1 to re-ingest daily data where appropriate, then audit the practical coverage and quality of the resulting HOSE dataset.

Decide the best implementation and execution plan yourself after inspecting the codebase, existing local data, provider limits, manifests, schemas, and current commands.

The result should answer, with evidence:

- which HOSE symbols are actually usable for future research;
- how much daily history is available and reliable per symbol;
- where data is missing, sparse, duplicated, stale, or inconsistent;
- whether unit provenance now permits VND traded-value calculations;
- what historical range and universe are realistic for the next phase;
- what unresolved data risks still block trustworthy feature or strategy work.

You may refactor, extend, or add commands, diagnostics, contracts, reports, tests, and documentation as needed.

## Non-negotiable constraints

- Do not implement strategy, signals, labels, backtesting, ML/HMM, portfolio logic, execution, or UI.
- Do not expose, print, log, commit, or include the API key or `.env`.
- Do not commit generated market data, manifests, reports, caches, or live responses.
- Do not fabricate point-in-time universe membership, adjustment semantics, corporate-action completeness, timezone semantics, or unit provenance.
- Keep tests offline by default.
- Any live API work must be explicit, rate-limit-aware, auditable, and conservative.
- Preserve raw-data immutability and existing provenance rules.
- Unknown or unsupported facts must remain explicit unknowns.

The API key is available locally in `.env`; use it only through the existing secure configuration path if live calls are necessary.

## Completion standard

Use your judgment to define and implement the smallest coherent solution that makes Phase 2.2 useful.

Run the full quality gate, perform suitable controlled live smoke or ingestion runs, inspect the outputs, and fix defects you discover.

Save this prompt as:

```text
docs/prompts/phase-2.2-reingestion-coverage-audit-prompt.md
```

Commit and push only after all checks pass and no sensitive or generated files are staged.

Return a concise completion report covering:

- decisions made and why;
- architecture or commands changed;
- live ingestion scope;
- coverage and quality findings;
- provenance verification results;
- tests and quality-gate results;
- unresolved risks;
- commit hash and push status.

Do not begin the next phase.
