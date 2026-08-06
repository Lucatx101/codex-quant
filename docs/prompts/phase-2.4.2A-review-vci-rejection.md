# Phase 2.4.2A Review — Verify VCI Rejection and Scope

Inspect the current uncommitted implementation and all generated evidence from the VCI source-qualification run.

Do not add new product scope. Do not commit or push.

## Objective

Determine whether the reported verdict:

```text
rejected
```

is genuinely supported by raw VCI evidence and the existing canonical daily-data contract, or whether it could be caused by an adapter, mapping, filtering, date-alignment, or qualification-logic defect.

The current result must be reviewed before Phase 2.4.2A is accepted.

## Required review

### 1. Verify raw-to-normalized correctness

Trace the VCI payload through:

```text
raw response
→ adapter mapping
→ normalization
→ validation
→ qualification verdict
```

For every mapped field, verify:

- raw field name;
- normalized field;
- data type;
- scaling/unit transformation;
- date/timestamp conversion;
- sorting/deduplication;
- local start/end filtering.

Confirm that no field swap, date shift, scale conversion, or adjustment assumption can explain the reported OHLC violations.

### 2. Forensic review of OHLC violations

Inspect all reported ABR OHLC violations from immutable raw evidence.

Produce a compact evidence table containing at least:

- symbol;
- date;
- raw open/high/low/close;
- normalized open/high/low/close;
- violated invariant;
- previous close where relevant;
- matching KBS row if available;
- whether the violation is reproducible from raw evidence.

Also report OHLC violation counts for every probed symbol, not only ABR.

Do not modify, clamp, or reinterpret values merely to make them pass.

### 3. Decompose KBS/VCI differences

The report states that 2,679 of 3,512 overlapping rows differ in at least one field.

Break this down by:

- field;
- symbol;
- absolute and relative magnitude;
- constant scaling patterns;
- likely adjustment-like patterns;
- volume-only versus price differences;
- exact-match rate by field.

Determine whether the differences look like:

- adjusted versus unadjusted history;
- unit mismatch;
- rounding;
- date alignment;
- provider-specific bars;
- unresolved semantics.

Do not claim a cause without evidence.

### 4. Review zero-volume and VCI-only dates

For HPX, BTT, and any other affected probes, verify whether zero-volume rows and VCI-only dates originate directly from the raw provider response.

Check whether they represent:

- valid zero-trade observations;
- suspended/non-tradable dates;
- calendar-filled synthetic bars;
- duplicated/reference-price records;
- local processing artifacts;
- unknown provider behavior.

Keep the cause `unknown` when evidence is insufficient.

### 5. Review request semantics and truncation risk

Verify the finding that `start` is not sent in the VCI payload and filtering is local.

Assess whether this behavior can cause:

- hidden truncation before local filtering;
- incomplete older history;
- false qualification results;
- inefficient or non-deterministic requests.

Confirm exactly what is known for 1,000-row and 1,200-row responses and what remains unknown above that range.

Do not run additional live calls unless an offline ambiguity directly blocks the verdict. If a live call is essential, justify it first, keep it minimal and bounded, and record it through the existing evidence workflow.

### 6. Audit verdict logic

Verify that the final verdict is deterministic and follows the documented criteria.

Clarify its scope. It should distinguish between:

```text
rejected_for_canonical_daily_ohlcv
```

and a blanket claim that VCI is unusable for every possible purpose.

Confirm whether Phase 2.4.2B should:

- exclude VCI from both primary and fallback daily-history roles;
- retain VCI only as non-authoritative diagnostic evidence;
- or reconsider the verdict because of an implementation defect.

### 7. Scope and code-quality review

The phase currently changes roughly 1,900 lines across multiple modules.

Review for:

- unnecessary abstraction;
- duplicated campaign/workflow logic;
- dead code;
- excessive coupling;
- changes outside source qualification;
- compatibility risk to existing KBS paths;
- insufficient tests around verdict boundaries.

Reduce the implementation only where doing so improves correctness or maintainability without deleting required evidence/provenance behavior.

### 8. Regression safety

Confirm:

- existing KBS campaign plan/state hashes are unchanged;
- no existing KBS behavior is altered;
- no generated market data or secrets are tracked;
- offline tests remain the default;
- live probes cannot be triggered accidentally.

Run:

```bash
make check
git diff --check
git status --short
```

Run focused tests for the qualification and adapter paths.

## Completion report

Return:

1. whether the VCI rejection is confirmed, revised, or invalidated;
2. exact evidence supporting that conclusion;
3. OHLC forensic summary;
4. KBS/VCI difference decomposition;
5. zero-volume/VCI-only date conclusion;
6. request/truncation risk conclusion;
7. any defects found and fixes made;
8. files changed during review;
9. tests/checks and results;
10. final `git status --short`;
11. whether Phase 2.4.2A is ready to commit.

Do not begin Phase 2.4.2B, point-in-time universe work, canonical assembly, features, strategies, or backtesting.
