# Phase 2.4.2A — VCI Source Qualification

You are working in the public repository:

```text
/Users/lucatxtruong/Documents/Codex-quant
```

## Mission

Inspect the repository, the current daily-ingestion architecture, campaign state, and the Phase 2.4.1 forensic evidence. Then design and execute a narrowly scoped qualification of the vnstock **VCI** daily-history backend.

The purpose is to determine whether VCI is technically and semantically suitable for a future canonical historical daily campaign.

Treat this as a source-qualification phase, not as a migration or ingestion campaign.

## Current state

- Phase 2.4.1 is complete.
- Existing campaign: `hose-daily-20260805-v1`.
- KBS evidence currently contains:
  - 251 `usable_vnd` symbols;
  - 119 quarantined failed tasks;
  - 17 blocked stale tasks;
  - no full-universe assembly;
  - no accepted research readiness.
- The KBS forensic audit found no mapping or normalization defect.
- Existing KBS data, manifests, receipts, campaign state, and forensic evidence must remain unchanged.

## Qualification questions

Build enough reproducible evidence to answer, at minimum:

1. **API/request semantics**
   - `start`/`end` inclusivity;
   - maximum rows or silent truncation behavior;
   - chunking requirements;
   - ordering, duplicates, pagination, and determinism;
   - empty-response behavior;
   - provider errors, retries, throttling, and rate-limit behavior observable from the client.

2. **Schema and unit semantics**
   - field mapping;
   - price units;
   - volume units;
   - timestamp/date semantics;
   - provider/backend provenance required to permit VND traded-value calculations;
   - whether semantics are documented, empirically inferred, or still unknown.

3. **OHLC integrity**
   - standard OHLC invariants;
   - duplicate symbol-date rows;
   - missing or malformed observations;
   - behavior on representative clean symbols;
   - behavior on representative KBS failed and stale cases;
   - behavior around listing, suspension, sparse-trading, and corporate-action edge cases where evidence is available.

4. **Adjustment semantics**
   - whether prices are adjusted or unadjusted;
   - whether adjustment behavior is stable across time and symbols;
   - comparison around known corporate-action dates where local evidence exists;
   - do not claim semantics that cannot be proven.

5. **Cross-source comparison**
   - compare VCI and KBS on a deliberately selected, small evidence set;
   - include representative clean, KBS-failed, KBS-stale, sparse-history, and edge-case symbols;
   - compare at the series/task level;
   - do not create a mixed canonical dataset and do not silently prefer one provider row by row.

## Engineering freedom

Choose the architecture, command surface, fixtures, report schema, sampling method, and refactors needed to produce strong evidence.

Prefer extending existing abstractions and provenance contracts over building a parallel ad hoc script.

Keep the implementation proportionate to source qualification. Do not build a full multi-provider campaign system unless the existing architecture clearly requires a small reusable extension.

## Hard constraints

- No look-ahead assumptions.
- No silent source mixing or overwrite.
- No relaxing the existing OHLC validator.
- No clamping or rewriting provider values.
- No retrying the existing 119 failed or 17 stale KBS tasks.
- No mutation of `hose-daily-20260805-v1`.
- Raw evidence is immutable.
- Generated evidence, responses, Parquet, manifests, and reports must remain Git-ignored.
- Never hardcode, print, log, commit, or expose `VNSTOCK_API_KEY`.
- Offline tests must remain the default.
- Any live VCI probes must be:
  - explicit;
  - bounded to the minimum useful sample;
  - sequential unless strong evidence justifies otherwise;
  - rate-limited;
  - resumable or safely repeatable;
  - fully recorded through provenance/manifests.
- Do not launch a 403-symbol VCI campaign.
- Unknown must remain `unknown`.

## Required outcome

Produce a reproducible qualification result with exactly one final verdict:

```text
qualified
qualified_with_constraints
rejected
unknown
```

The verdict must be evidence-based and accompanied by:

- supported capabilities;
- discovered constraints;
- unresolved semantics;
- observed failure modes;
- unit/provenance decision;
- adjustment-semantics decision;
- comparison with relevant KBS forensic cases;
- explicit recommendation for whether Phase 2.4.2B may evaluate VCI as a primary source, fallback source, or neither.

A `qualified` verdict is not allowed if adjustment semantics, price/volume units, or truncation behavior remain materially unresolved.

## Non-goals

Do not:

- choose or implement the final cross-source policy;
- replace KBS;
- assemble a canonical dataset;
- run a full-universe campaign;
- define the final research universe;
- solve point-in-time membership;
- implement corporate-action processing;
- create features, labels, signals, strategies, backtests, ML/HMM, portfolio logic, execution, or UI.

## Tests and validation

Add meaningful offline regression tests for any new behavior, including provider error/empty mappings and provenance contracts.

Separate live qualification probes from offline tests.

Before completion, run the repository's authoritative checks, including at least:

```bash
make check
```

Also run any focused tests or CLI dry-runs needed to prove the qualification workflow.

Do not weaken tests to obtain a pass.

## Completion standard

Finish only when:

- the qualification workflow is reproducible;
- live evidence is bounded and attributable;
- the verdict follows mechanically from documented evidence;
- all new data semantics are explicit;
- KBS campaign state is unchanged;
- generated artifacts are ignored;
- repository checks pass;
- no phase boundary has been crossed.

At the end, report concisely:

1. what was implemented;
2. live probes actually executed and provider-call count;
3. key empirical findings;
4. final VCI verdict and why;
5. unresolved risks;
6. files changed;
7. checks/tests run and results;
8. `git status --short`;
9. confirmation that no secret or generated market data is tracked.

Do not commit or push. Leave the reviewed implementation in the working tree.
