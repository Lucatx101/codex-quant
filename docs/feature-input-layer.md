# Feature Input Layer

Phase 2 converts local normalized Parquet into versioned, research-ready inputs. It does not
fetch live data or implement features, labels, strategies, backtests, portfolios, execution, ML,
or UI code.

## Architecture

The layer extends the existing data architecture instead of creating a second pipeline:

- `contracts.py` defines versioned universe, daily-panel, liquidity, availability, coverage, and
  market-time contracts.
- `feature_inputs.py` contains deterministic DataFrame transformations and report rendering.
- `unit_provenance.py` owns the versioned daily-unit registry and derives effective monetary
  permission from selected dataset rows.
- `market_time.py` defines HOSE time conventions and preserves timestamp provenance.
- `validators.py` enforces contract invariants and returns existing structured validation models.
- `workflows.py`, `storage.py`, and `manifests.py` provide local I/O and provenance through the
  same mechanisms as Phase 1.
- `campaigns.py` reconstructs immutable symbol/chunk campaign state, audits compatible sources,
  and assembles `assembled-daily-v1` only after every task is resolved.

The canonical daily feature input is long-form, keyed and ordered by `symbol,date`. Long form
preserves sparse observations without inventing bars and fits the existing symbol-partitioned
normalized storage.

## Commands

The feature-input commands below read local normalized data and make zero provider calls.

```bash
# Latest local snapshot, with no historical membership claim
python3 -m hose_quant.cli data prepare-universe --exchange HOSE

# Select an observed snapshot and record a separate, unverified research reference date
python3 -m hose_quant.cli data prepare-universe \
  --snapshot-date 2026-07-04 \
  --reference-date 2025-12-31

# Screen candidates using data available through the stated liquidity reference date
python3 -m hose_quant.cli data prepare-universe \
  --snapshot-date 2026-07-04 \
  --with-liquidity \
  --liquidity-reference-date 2026-07-02 \
  --window-weekdays 20 \
  --min-history-observations 15 \
  --min-trading-frequency 0.8 \
  --max-zero-volume-frequency 0.2 \
  --min-average-volume 100000

# Build a panel plus per-symbol availability diagnostics
python3 -m hose_quant.cli data build-daily-panel \
  --symbols FPT,HPG,VCB \
  --start 2026-05-04 \
  --end 2026-07-02

# Audit exactly one successful provenance-aware daily run against a current snapshot
python3 -m hose_quant.cli data audit-daily-coverage \
  --daily-run-id 20260805T000000Z-backfill-daily \
  --start 2020-01-01 \
  --end 2026-08-04 \
  --snapshot-date 2026-08-05
```

`prepare-universe --help` lists every configurable threshold. Omitting `--symbols` from
`build-daily-panel` selects all symbols present in local daily storage.

`audit-daily-coverage` refuses a missing, failed, or non-backfill source manifest and reads only
Parquet files whose filename matches the requested `daily_run_id`. It never merges legacy and new
runs implicitly.

## Universe Contract

The universe operation selects one actually observed provider snapshot. Each selected input row
produces one output row with `input_row_number`, normalized/raw symbol evidence, provider security
type, candidate status, and structured reasons.

- `included_candidate` means only that the provider reported a stock-like type and basic record
  validation passed. It does not mean common stock, actively listed, or tradable.
- Known non-stock types are `excluded` with a provider-type reason.
- Duplicates and unsupported classifications are `uncertain`; malformed symbols and exchange
  mismatches are excluded.
- `source_snapshot_observed_at_utc` records when the snapshot was observed.
- Raw snapshot time, timezone-awareness status, interpretation, and localization status are also
  retained; a naive snapshot observation is rejected rather than assumed to be UTC.
- `requested_reference_date` is research metadata only.
- `historical_membership_verified` is always false for the current provider snapshot.
- A reference date different from the snapshot date forces
  `historical_membership_status=requested_reference_unverified`.

This makes survivorship risk visible but does not solve it. A true historical membership source
is still required before point-in-time universe claims are possible.

## Liquidity Contract

Liquidity is characterized on a trailing window of expected weekdays ending at
`reference_date`. Only observations on or before that date are used. The current expectation does
not remove Vietnamese holidays, exchange closures, or symbol-specific halts.

Per-symbol output includes observed dates, positive/zero-volume counts, trading frequency,
zero-volume frequency, average provider-reported volume, recent valid close, missing-data status,
insufficient-history status, screening outcome, and reasons. Weekend observations are not used in
the window and cause a failed screen with an auditable reason.

Screening thresholds are configurable through CLI options or these environment settings:

- `LIQUIDITY_WINDOW_WEEKDAYS`
- `LIQUIDITY_MIN_HISTORY_OBSERVATIONS`
- `LIQUIDITY_MIN_TRADING_FREQUENCY`
- `LIQUIDITY_MAX_ZERO_VOLUME_FREQUENCY`
- `LIQUIDITY_MIN_AVERAGE_VOLUME`
- `LIQUIDITY_MIN_AVERAGE_TRADED_VALUE_VND`

Unit policy is not a user-selectable setting. The CLI has no `--unit-policy` option. For every
selected set of daily rows, the resolver reads the dataset metadata and requires one identical,
complete provenance record that exactly matches a registered contract. The current eligible
record is:

- provenance schema `daily-unit-provenance-v1`;
- `provider=vnstock`, `data_backend=kbs`, and `source_resolution=1D`;
- unit policy `vnstock-kbs-daily-ohlcv`, version `1`;
- source price unit `thousand_vnd` with VND scale `1000`;
- source volume unit `shares` with share scale `1`;
- evidence reference `vnstock-kbs-ohlcv-units@2026-01-31`.

The provider adapter uses the same KBS backend identifier for the official OHLCV call and passes
that registered record to the normalizer. The normalizer writes the version, backend, declared
units, scales, and evidence reference into each new normalized row. The feature layer then checks
the complete record instead of trusting a CLI flag, filename, configuration value, or prose.
This implements the chain `provider call -> normalized provenance -> verified interpretation ->
monetary calculation`.

The unit interpretation is grounded in the official
[Vnstock Market schema](https://vnstocks.com/docs/vnstock-data/cau-truc-du-lieu/market), which shows
decimal OHLCV price and integer traded volume, and the official
[Vnstock release history](https://vnstocks.com/docs/vnstock-insider-api/lich-su-phien-ban), which
documents KBS history prices in thousand VND. Those documents support the registered contract,
but documentation text by itself never verifies a dataset.

Legacy Phase 1 files have no source-specific provenance. They remain usable for daily panels,
availability diagnostics, average provider volume, trading frequency, and zero-volume frequency.
Their status is `legacy_missing` and unverified, `average_traded_value_vnd` remains null, and a VND
threshold fails with a remediation message. Incomplete, mixed, or incompatible records are also
unverified. Existing files are never rewritten and no live call is made to repair provenance.
Re-ingest through the provenance-aware daily provider workflow to produce a future dataset that
can qualify for verified VND calculations.

## Daily Panel

The `daily-panel-v2` contract requires provider, data backend, symbol, exchange, date, OHLCV,
source resolution, ingestion provenance, source unit-provenance fields, effective policy
name/version, provenance and verification statuses, verification reason, evidence reference, and
the explicit `vnd_traded_value_permitted` boolean. It enforces deterministic ordering, unique
`symbol,date` keys, nonnegative and integer-like volume, valid OHLC relationships, and agreement
between effective unit metadata and the provenance resolved from the rows.

The panel contains observed provider rows only. It does not create a rectangular symbol/date grid,
forward-fill values, synthesize market bars, or construct adjusted prices. Missing source values
remain null. `price_adjustment_status` is `unknown` unless a provider flag exists, in which case it
is still `provider_flag_unverified` rather than treated as authoritative.

## Availability Diagnostics

Every requested symbol receives a diagnostic row, including symbols with no data. Diagnostics
include observed range, observation and duplicate counts, missing/invalid OHLC counts, zero-volume
count, weekend observations, expected weekdays, missing expected weekdays, and coverage ratio.

The expected-session model is deliberately named `weekdays_only`. Vietnamese public holidays are
not silently classified as exchange-confirmed missing sessions; reports state that the holiday
calendar is incomplete.

## Daily Coverage Audit

The `daily-coverage-v1` contract compares one successful normalized daily run with included stock
candidates from one observed HOSE universe snapshot. Every current candidate receives a row even
when it has no daily data. Symbols observed in the run but absent from the selected current
snapshot remain visible as `not_in_selected_current_snapshot` rather than being silently dropped.

Per-symbol diagnostics include first and last dates, observations, duplicate and conflicting
dates, invalid dates and OHLCV, zero-volume frequency, weekend rows, weekday span coverage,
longest missing weekday streak, staleness, source file count, exact unit policy, and explicit
research-usability flags. Status is one of `absent`, `blocking_quality_issues`, `stale`,
`not_ingested`, `insufficient_history`, `sparse`, `usable_non_monetary`, or `usable_vnd`.
`not_ingested` means the symbol was outside the source manifest request; `absent` means it was
requested but produced no observations.

Defaults require 500 observations, 90 percent weekday coverage within the observed span, no more
than 20 percent zero-volume rows, and a last observation no more than seven calendar days before
the requested audit end. Weekday coverage is a diagnostic approximation: Vietnamese holidays,
exchange closures, and symbol halts are not removed.

`usable_vnd` means raw OHLCV passed those thresholds and every selected symbol row carries the
registered KBS unit provenance. It does not verify adjusted-price semantics, corporate-action
completeness, historical membership, or survivorship-safe research. The corresponding adjusted
price and point-in-time universe usability fields always remain false.

## Market Time

The target convention is `Asia/Ho_Chi_Minh`. Daily dates remain unlocalized provider trading-date
labels. Timezone-aware intraday timestamps retain their source timezone; timezone-naive values
retain their raw value, `naive` status, provider context, and `localization_applied=false`.

The current HOSE schedule policy records opening auction 09:00-09:15, continuous morning
09:15-11:30, lunch break 11:30-13:00, continuous afternoon 13:00-14:30, closing auction
14:30-14:45, and negotiated trading through 15:00. The policy follows official HOSE trading rules
([trading rules](https://staticfile.hsx.vn/Uploads/LocalFiles/993f05f252bb4bf0a755bcd51440f90f/20210704_Quy%20ch%E1%BA%BF%20giao%20d%E1%BB%8Bch.pdf),
[session table](https://staticfile.hsx.vn/Uploads/UploadDocuments/2372196/2.Thoi%20gian%20giao%20dich.pdf))
but is not a complete exchange calendar; restricted instruments may use different schedules.

## Outputs And Provenance

Generated files are ignored by Git:

```text
data/feature_inputs/vnstock/universe/snapshot_date=YYYY-MM-DD/*.parquet
data/feature_inputs/vnstock/daily_panel/start_date=YYYY-MM-DD/end_date=YYYY-MM-DD/*.parquet
data/feature_inputs/vnstock/liquidity/reference_date=YYYY-MM-DD/*.parquet
data/feature_inputs/vnstock/availability/start_date=YYYY-MM-DD/end_date=YYYY-MM-DD/*.parquet
data/feature_inputs/vnstock/coverage/snapshot_date=YYYY-MM-DD/start_date=YYYY-MM-DD/end_date=YYYY-MM-DD/*.parquet
data/campaigns/vnstock/daily/campaign_id=<campaign-id>/...
data/assembled/vnstock/daily/campaign_id=<campaign-id>/dataset_id=<dataset-id>/...
reports/feature_inputs/*-availability.json
reports/feature_inputs/*-availability.md
reports/data_quality/*-daily-coverage.json
reports/data_quality/*-daily-coverage.md
data/manifests/<run_id>.json
```

Manifests include input paths, output paths, parameters, contract versions, package/Git provenance,
row counts, validation summary, notes, and provider call count. Daily backfill and local
feature-input manifests also carry the effective unit-provenance object: provider/backend,
provenance status, source record when complete, policy name/version, verification status,
evidence reference, reason, and VND permission. Phase 2 local operations set provider call count
to zero.

## Known Risks

- Historical membership, delisting dates, active-trading state, and survivorship-safe universe
  data are unavailable.
- Price adjustment and corporate-action completeness remain unverified.
- The weekday calendar omits Vietnamese holidays, ad hoc closures, and symbol halts.
- Legacy normalized daily files lack source-specific unit provenance.
- Legacy intraday files predate the Phase 2 raw timestamp/awareness fields and remain explicitly
  unresolved rather than being relocalized.

The full-universe campaign, resume model, campaign audit, and assembled daily contract are
documented in [universe-ingestion-campaign.md](universe-ingestion-campaign.md).
