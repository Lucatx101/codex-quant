# Phase 2.4 — Execute and Complete the HOSE Daily Campaign

Work in:

```text
/Users/lucatxtruong/Documents/Codex-quant
```

Continue from the current `main` branch and inspect the repository, campaign state, manifests, local generated data, and existing runbooks before acting.

## Mission

Execute the existing HOSE daily ingestion campaign to completion as safely and efficiently as the current system allows.

Use the campaign engine already implemented. Do not redesign the architecture unless live execution exposes a real defect that blocks correctness, resumability, provenance, or safety.

Operate autonomously:

- inspect the current campaign state;
- choose appropriate batch sizes and pacing;
- run live batches;
- resume without repeating completed work;
- stop and diagnose provider, quota, provenance, compatibility, or validation failures;
- retry only when evidence supports retry;
- keep campaign state reconstructible and auditable;
- continue until every task is resolved or a genuine external blocker prevents completion;
- audit the completed campaign;
- assemble only when the system's completion and compatibility rules permit it;
- evaluate research readiness using the existing explicit policy.

Make reasonable operational decisions yourself. Do not ask for approval for routine batch execution, retries, diagnostics, or local validation.

## Non-negotiable constraints

- Do not implement features, signals, labels, strategy, backtesting, ML/HMM, portfolio logic, execution, or UI.
- Do not expose, print, log, commit, or embed `.env`, API keys, credentials, or secrets.
- Do not commit generated market data, campaign state, receipts, manifests, reports, caches, raw responses, or assembled datasets.
- Preserve raw immutability, provenance, resumability, compatibility, atomic publication, and readiness semantics from prior phases.
- Do not bypass provider limits or disable safety controls merely to finish faster.
- Do not mark failed, partial, stale, incompatible, or missing work as complete.
- Do not fabricate historical universe membership, adjusted-price semantics, corporate-action completeness, holiday coverage, or provider behavior.
- Unknowns and external blockers must remain explicit.

The API key is available only through the existing secure local configuration path. Never reveal its value.

## Completion standard

The phase is complete only when one of these is true:

1. The campaign is fully resolved, audited, assembled where allowed, and its research-readiness status is evaluated; or
2. A genuine external blocker prevents completion, and the remaining state, evidence, retry path, and exact blocker are clearly documented.

Use the full repository quality gate after any code change. If no code change is required, still validate the final campaign state and generated evidence carefully.

Fix implementation defects discovered during live execution only when necessary for correctness or safe completion. Keep such fixes minimal, tested, documented, and committed separately from generated data.

Save this prompt as:

```text
docs/prompts/phase-2.4-execute-hose-daily-campaign-prompt.md
```

Commit and push source, tests, and documentation only if they changed and all checks pass.

Return a concise completion report covering:

- starting and final campaign state;
- live batches and provider-call usage;
- completed, empty, failed, stale, incompatible, and pending tasks;
- retries and reasons;
- coverage and provenance findings;
- assembly result;
- readiness assessment;
- defects found and fixes made;
- quality-gate results;
- unresolved external blockers;
- commit hash and push status, if code changed;
- confirmation that secrets and generated data remained uncommitted.

Do not begin the next phase.
