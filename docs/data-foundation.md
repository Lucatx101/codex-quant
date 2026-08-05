# Data Foundation

Phase 1 implements the data layer for `vnstock` only. It prepares raw and normalized market data for future feature engineering and backtesting, but it does not implement features, strategies, a backtester, live trading, or a web UI.

## Storage Layout

Generated data is ignored by Git.

```text
data/
├── raw/vnstock/<dataset>/<run_id>/raw.jsonl
├── normalized/vnstock/universe/snapshot_date=YYYY-MM-DD/*.parquet
├── normalized/vnstock/daily/symbol=FPT/*.parquet
├── normalized/vnstock/intraday/resolution=1m/symbol=FPT/trading_date=YYYY-MM-DD/*.parquet
├── normalized/vnstock/quotes/snapshot_date=YYYY-MM-DD/*.parquet
├── campaigns/vnstock/daily/campaign_id=<campaign-id>/...
├── assembled/vnstock/daily/campaign_id=<campaign-id>/dataset_id=<dataset-id>/...
├── cache/
└── manifests/<run_id>.json
```

## Datasets

- Universe snapshots preserve provider, exchange, symbol, organization names, security type, provider ID, raw exchange/type fields, and snapshot time.
- Daily OHLCV stores provider, symbol, nullable exchange, date, OHLCV, unknown adjusted flag, source resolution, and ingestion time.
- Intraday bars store provider, symbol, nullable exchange, timestamp, trading date, resolution, OHLCV, volume, unknown volume semantics, unknown bar status, and ingestion time.
- Quote snapshots preserve provider raw time as `provider_time_raw`. Numeric provider time is marked `provider_specific_unparsed` because official docs did not verify unit semantics.

## Validation

Validation returns structured results with dataset, severity, check name, message, affected columns, affected row count, safe sample keys, and whether the issue blocks output.

Implemented checks include required columns, duplicate keys, OHLC relationships, non-negative prices and volumes, integer-like daily volume, future daily dates, intraday timestamp parsing, timezone-naive intraday timestamps, quote missing symbols, quote provider-time preservation, and generated data-quality reports.

## Commands

```bash
python3 -m hose_quant.cli data fetch-universe --exchange HOSE
python3 -m hose_quant.cli data backfill-daily --symbols FPT,HPG,VCB --start 2020-01-01 --end 2026-07-03 --chunk-calendar-days 730
python3 -m hose_quant.cli data fetch-intraday --symbols FPT --resolution 1m --lookback-days 1
python3 -m hose_quant.cli data snapshot-quotes --symbols FPT,HPG,VCB
python3 -m hose_quant.cli data validate
```

Use `--dry-run` where available to avoid provider calls. Live fetch commands require `VNSTOCK_API_KEY`.

## Safety Limits

Quote and multi-symbol fetch commands default to a small symbol limit (`MAX_QUOTE_SYMBOLS`,
default 20). Larger requests require `--allow-large-universe`. Batch quotes use the documented
batch quote method instead of per-symbol quote loops.

Daily backfills split each symbol range into non-overlapping date chunks. The default chunk is
730 calendar days, each provider request asks for at most 1,000 bars, and a response that reaches
that boundary fails rather than silently claiming complete coverage. The workflow projects calls
before execution, defaults to at most 40 planned wrapper calls, and spaces wrapper attempts by at
least 2.1 seconds while retaining a post-success delay across command boundaries. These defaults
leave a conservative margin under the documented community limit of 60 requests per minute.

`provider_call_count` records attempts made by this project's wrapper. Vnstock can perform its own
HTTP retries inside one wrapper call, so the field is auditable workflow evidence but not an exact
measurement of free-tier HTTP quota consumption.

Raw responses are retained for diagnosis. Normalized partitions are published only when every
planned chunk completes without an exception and the combined frame passes validation. A failed
run therefore cannot be selected later as a complete normalized audit source.

Daily re-ingestion is fail-fast: the first exhausted chunk error stops remaining provider calls.
This avoids spending quota on a run that can no longer be published as complete.

If Vnstock attempts to terminate the process on a quota failure, the adapter converts that
`SystemExit` into a typed provider error. Daily re-ingestion stops remaining calls, retains
completed raw responses, and writes a failed manifest.

## Unresolved Issues

- Historical point-in-time universe membership is not verified.
- Adjusted-price semantics are unknown.
- Corporate-action completeness is unknown because the Phase 0 FPT events check returned empty.
- Free-tier minute polling practicality and full batch quote limits require more live evidence.
- No free-tier WebSocket interface is verified.

## Phase 2

The local feature-input layer built on these normalized datasets is documented in
[feature-input-layer.md](feature-input-layer.md). It does not change the unresolved historical
membership, adjustment, or corporate-action limitations of this foundation. Provenance-aware KBS
daily ingestion now records a versioned unit contract on new normalized rows; legacy Phase 1 files
remain unchanged and explicitly ineligible for VND liquidity calculations.

The Phase 2.2 exact-run re-ingestion and coverage workflow is documented in
[reingestion-coverage-audit.md](reingestion-coverage-audit.md).

Phase 2.3 keeps each bounded daily task as an immutable Phase 1-style raw/normalized run, then
adds reconstructible campaign receipts, compatibility assessment, campaign coverage, and atomic
versioned assembly. The campaign never rewrites old source runs or silently combines incompatible
contracts. See [universe-ingestion-campaign.md](universe-ingestion-campaign.md).
