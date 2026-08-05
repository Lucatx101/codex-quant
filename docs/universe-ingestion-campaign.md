# Universe Ingestion Campaign

Phase 2.3 turns the daily pilot into a resumable campaign over the usable stock candidates in one
observed HOSE universe snapshot. It adds no features, strategy, backtest, ML, portfolio,
execution, live-trading, or UI behavior.

## Provider Basis

The campaign reuses the existing official Vnstock Unified UI call:

```python
Market().equity(symbol).ohlcv(
    start="YYYY-MM-DD",
    end="YYYY-MM-DD",
    resolution="1D",
    count=1000,
    source="kbs",
)
```

The installed Vnstock 4.0.4 method exposes those parameters. The official
[Market data documentation](https://vnstocks.com/docs/vnstock/du-lieu-thi-truong-market-data)
documents equity OHLCV and its date, resolution, and count controls, while the official
[data-source guide](https://vnstocks.com/docs/vnstock-data/data-sources) identifies KBS as an
OHLCV source. Campaign orchestration does not invent a batch endpoint. Each task is one bounded
official OHLCV request.

The community package advertises 60 requests per minute. Defaults remain conservative:

- date chunks are 730 calendar days and cannot exceed 1,095 days;
- each provider response is rejected at the 1,000-row truncation boundary;
- `CAMPAIGN_MAX_TASKS_PER_RUN=20`;
- `MAX_RETRY_ATTEMPTS=2`, so one batch has at most 40 wrapper attempts;
- `MAX_LIVE_PROVIDER_CALLS=40` and `PROVIDER_SLEEP_SECONDS=2.1`;
- a campaign batch stops at its first failed child ingestion.

Vnstock may make internal HTTP retries that this wrapper cannot observe, so wrapper attempt count
is useful audit evidence but not an exact provider-side quota measurement.

## State Model

Campaign state is generated and ignored by Git:

```text
data/campaigns/vnstock/daily/campaign_id=<campaign-id>/
├── plan.json
├── state.json
├── operation.lock
├── receipts/task_id=<symbol__start__end>/<source-run-id>.json
└── audits/<audit-run-id>-coverage.parquet
```

`plan.json` is immutable. Initialization fails if that campaign ID already exists. It freezes:

- the actual universe input paths, source run IDs, and observed snapshot timestamp;
- sorted included stock-candidate symbols;
- campaign dates and contiguous, non-overlapping chunks for every symbol;
- provider `vnstock`, backend `kbs`, resolution `1D`, and normalized contract version;
- the exact registered KBS daily-unit provenance;
- null, unknown provider adjustment semantics;
- the task-staleness threshold.

A task key is `SYMBOL__YYYY-MM-DD__YYYY-MM-DD`. Each provider attempt remains an ordinary immutable
`data backfill-daily` run with raw evidence, an all-or-nothing normalized publication, and its own
manifest. A tiny receipt links that source run to one campaign task. `state.json` is a derived
index, not the source of truth: it is rebuilt from the immutable plan, receipts, child manifests,
and normalized files.

Task status is one of:

- `pending`: no receipt exists;
- `complete`: one compatible source contains validated observations through the chunk boundary;
- `empty`: the provider call succeeded and explicitly returned no observations;
- `failed`: the child run failed and published no normalized task output;
- `stale`: observations exist but end too far before the task boundary;
- `incompatible`: manifest, source, contract, range, provenance, adjustment, or uniqueness checks
  do not permit the source to participate.

At symbol level, a mixture of resolved and unresolved chunks is `partial`. A symbol is `complete`
only when all its tasks are `complete` or provider-confirmed `empty`.

## Commands

Create a full plan from one local normalized snapshot. This command makes no provider calls:

```bash
python3 -m hose_quant.cli data init-daily-campaign \
  --campaign-id hose-daily-20260805 \
  --snapshot-date 2026-08-05 \
  --start 2020-01-01 \
  --end 2026-08-04 \
  --chunk-calendar-days 730
```

Attach an existing successful Phase 2.2 run when it aligns to complete campaign chunks:

```bash
python3 -m hose_quant.cli data adopt-daily-run \
  --campaign-id hose-daily-20260805 \
  --daily-run-id 20260805T034049Z-backfill-daily
```

Preview the next resumable batch without credentials or provider calls:

```bash
python3 -m hose_quant.cli data run-daily-campaign \
  --campaign-id hose-daily-20260805 \
  --max-tasks 20 \
  --dry-run
```

Run exactly that bounded class of work after reviewing the plan:

```bash
python3 -m hose_quant.cli data run-daily-campaign \
  --campaign-id hose-daily-20260805 \
  --max-tasks 20
```

Completed and provider-empty tasks are skipped automatically. Pending work continues on the next
invocation. Retrying non-pending evidence is always explicit:

```bash
python3 -m hose_quant.cli data run-daily-campaign \
  --campaign-id hose-daily-20260805 \
  --retry-failed \
  --retry-stale \
  --retry-incompatible
```

Audit the campaign without a provider call. Threshold flags match `audit-daily-coverage`:

```bash
python3 -m hose_quant.cli data audit-daily-campaign \
  --campaign-id hose-daily-20260805
```

Assembly is a separate local command and is refused until every task resolves:

```bash
python3 -m hose_quant.cli data assemble-daily-campaign \
  --campaign-id hose-daily-20260805
```

## Resume And Recovery

The current batch holds an advisory campaign lock so two processes cannot select the same pending
work concurrently. Every child manifest declares its campaign and task ID. If the process stops
after writing a child manifest but before writing its receipt, the next state assessment scans
manifests and reconstructs the missing receipt. A completed child therefore is not called again.

If interruption occurs before a child manifest can be durably written, no trustworthy completion
evidence exists and the task remains pending. Repeating that provider call is safer than claiming
completion. `SIGKILL`, host failure, disk failure, and provider-side work completed without local
evidence remain unavoidable recovery limits.

Failed, stale, and incompatible attempts remain immutable evidence. A later compatible retry can
be selected for that task. If more than one compatible successful source is attached to the same
task, state becomes `incompatible`; the system does not silently pick one. There is intentionally
no command that deletes or rewrites receipts.

## Adoption And Compatibility

An adopted run must be a successful `data backfill-daily` manifest whose symbols and outer date
range align exactly with whole campaign chunks. Every selected task then checks:

- provider, exchange, optional declared resolution, and requested date coverage;
- normalized daily contract version when declared;
- one source partition for the task symbol;
- no invalid dates, wrong symbols, wrong exchange, or rows outside the manifest range;
- daily OHLCV validation and unique `symbol,date` keys;
- one exact registered KBS provider/backend/unit provenance record;
- the campaign's unknown and null adjustment flag semantics;
- the configured task-end staleness threshold.

Compatible Phase 2.2 runs that predate the `normalized-daily-v2` manifest declaration can be
adopted only when their actual normalized rows pass the current structural, provenance, range,
and adjustment checks. The assessment records `legacy_contract_structurally_validated` rather
than silently pretending the old manifest declared a newer contract. Provider-empty sources must
declare the current contract and semantics because no rows exist to validate structurally.

An incompatible adoption attempt retains receipts so the affected tasks and reason codes remain
visible, but those sources cannot enter coverage or assembly.

## Campaign Audit

The `daily-campaign-audit-v1` report contains the immutable plan, every task assessment, symbol
status, source runs, provider-call-independent coverage rows, task/range overlap facts, duplicate
counts, and known risks. It forms a virtual daily source from complete symbols only. Unresolved
symbols remain visible as `not_ingested`; provider-confirmed empty symbols become `absent`.

`canonical_candidate=true` means only that all tasks resolved, compatible source rows are unique,
and those rows have one registered VND-capable unit provenance. Coverage status still determines
which individual symbols pass raw OHLCV and VND-liquidity thresholds. The flag does not establish:

- historical point-in-time membership or survivorship safety;
- listing, delisting, halt, or active-tradability history;
- adjusted-price semantics or corporate-action completeness;
- a complete Vietnamese exchange holiday calendar.

Those limitations stay structural in coverage outputs and written reports.

## Assembly Rules

`assemble-daily-campaign` publishes nothing unless every task is `complete` or `empty`. It then:

1. selects rows only inside each task's exact range;
2. rejects multiple successful sources or any duplicate `symbol,date` key;
3. rebuilds and validates `daily-panel-v2` without synthetic bars or forward filling;
4. adds source run and normalized-path lineage to every row;
5. validates `assembled-daily-v1` row preservation, IDs, uniqueness, and lineage;
6. derives a deterministic dataset ID from the immutable plan and selected task sources;
7. writes symbol Parquet plus metadata to a staging directory and atomically renames it;
8. validates an existing same-ID publication against deterministic content on repeated assembly.

Outputs are versioned and generated:

```text
data/assembled/vnstock/daily/
└── campaign_id=<campaign-id>/
    └── dataset_id=assembled-daily-v1-<digest>/
        ├── dataset.json
        └── symbol=<SYMBOL>.parquet

reports/data_quality/campaigns/<campaign-id>/<audit-run-id>.json
reports/data_quality/campaigns/<campaign-id>/<audit-run-id>.md
data/manifests/<operation-run-id>.json
```

There is no mutable `latest` or canonical alias in Phase 2.3. Publication makes a dataset eligible
for deliberate promotion in a later research phase; it does not promote it automatically.

## Remaining Risks

- The current universe snapshot creates survivorship risk for historical research.
- Provider response completeness below the 1,000-row boundary is not independently reconciled
  against an official symbol-level exchange calendar.
- Weekdays omit Vietnamese holidays, closures, and symbol halts.
- Adjustment and corporate-action semantics remain unknown.
- Multiple compatible source receipts require operator investigation or a clean campaign; they
  are never silently resolved.
- Full-universe completion requires many explicit live batches and is not implied by a pilot run.
