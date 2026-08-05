# Re-ingestion And Coverage Audit

Phase 2.2 makes daily data re-ingestion and practical HOSE coverage auditable. It does not add
features, strategies, labels, backtests, ML, portfolios, execution, or UI code.

## Why Re-ingestion Is Chunked

The official Vnstock market-data method accepts `start`, `end`, `resolution`, and `count`. A live
Phase 2.2 smoke request over a long range returned exactly the requested 1,000-bar count and only
the most recent part of the range. A single successful response therefore is not evidence that a
multi-year request is complete.

`data backfill-daily` now splits each symbol into contiguous, non-overlapping date chunks. The
default is 730 calendar days and the maximum accepted override is 1,095 days. Every request still
uses the official `Market().equity(symbol).ohlcv(...)` path with KBS as the declared source. If a
response reaches 1,000 rows, the run fails and asks for a smaller chunk rather than accepting a
possibly truncated result.

The Vnstock community tier documents a maximum of 60 requests per minute. The workflow therefore:

- projects `symbols x chunks` before the first call;
- defaults to at most 40 planned wrapper calls per command;
- spaces wrapper attempts by at least 2.1 seconds and delays after successful calls;
- requires the explicit `--allow-large-universe` override above the call or symbol safety limit;
- records projected, successful, empty, actual, retry, and pacing details in the manifest.

Official references:

- [Vnstock market data](https://vnstocks.com/docs/vnstock/du-lieu-thi-truong-market-data)
- [Vnstock community request limit](https://vnstocks.com/docs/vnstock-data/gioi-thieu-vnstock-data)

The manifest's `provider_call_count` is the number of wrapper attempts. Vnstock may retry HTTP
requests internally, so this is not guaranteed to equal provider-side quota consumption. That
remaining uncertainty is why the planned-call default stays below the documented minute limit.

## Publication Rule

Every provider response is written to the immutable raw run with its symbol and requested chunk
bounds. Normalized symbol partitions are published only when all calls complete without errors and
the combined dataset passes validation. Failed runs keep their raw evidence and failed manifest,
but do not expose partial normalized partitions as a complete source.

The first exhausted chunk error stops the run immediately because subsequent successful calls
could not make an all-or-nothing normalized publication complete.

Some Vnstock quota failures terminate the caller through `SystemExit`. The adapter converts that
behavior into a typed provider error; the workflow then stops remaining calls, retains completed
raw responses, and writes a failed manifest instead of disappearing mid-run.

The normalizer writes the registered daily-unit provenance into every newly ingested row. For the
current official KBS path, that includes thousand-VND price units and share volume units. VND
permission is still resolved from stored rows later; it is never inferred from a filename, command
flag, or documentation alone.

## Commands

First preview quota impact without a provider call:

```bash
python3 -m hose_quant.cli data backfill-daily \
  --symbols FPT,GAS,HPG,MSN,MWG,SSI,VCB,VHM,VIC,VNM \
  --start 2020-01-01 \
  --end 2026-08-04 \
  --chunk-calendar-days 730 \
  --dry-run
```

Remove `--dry-run` only after inspecting the projected call count. The successful manifest prints
the run ID needed by the local audit:

```bash
python3 -m hose_quant.cli data audit-daily-coverage \
  --daily-run-id RUN_ID \
  --start 2020-01-01 \
  --end 2026-08-04 \
  --snapshot-date 2026-08-05
```

The audit makes zero provider calls. It requires an exact successful `data backfill-daily`
manifest and reads only normalized daily files from that run. This prevents accidental mixing of
legacy rows, different ingestion dates, or incompatible provenance.

## Coverage Contract

`daily-coverage-v1` audits included stock candidates from one actually observed current HOSE
snapshot. It also retains symbols found in the daily run but not in that snapshot. It does not
backdate current membership or claim historical delisting coverage.

The default usable-raw threshold requires:

- at least 500 unique observation dates;
- at least 90 percent weekday coverage between first and last observation;
- no blocking duplicate, date, OHLC, volume, or weekend issues;
- no more than 20 percent zero-volume observations;
- a last observation within seven calendar days of the audit end.

`usable_vnd` additionally requires verified registered unit provenance. `usable_non_monetary`
passes raw coverage but cannot support VND traded-value calculations. Other statuses distinguish
symbols not requested by the source run, requested symbols with no observations, blocking quality
issues, stale data, insufficient history, and sparse coverage. This prevents a deliberately small
pilot from mislabeling the rest of HOSE as provider-empty data.

Outputs are generated and ignored by Git:

```text
data/feature_inputs/vnstock/coverage/.../*.parquet
reports/data_quality/*-daily-coverage.json
reports/data_quality/*-daily-coverage.md
data/manifests/*-audit-daily-coverage.json
```

The JSON and Markdown reports identify usable symbols, common usable date overlap, missing and
sparse symbols, duplicate/conflicting rows, staleness, source files, and unit provenance.

## Deliberate Unknowns

- Weekdays are only an approximation; Vietnamese holidays, closures, and symbol halts are not
  modeled.
- The selected universe is a current snapshot, not point-in-time historical membership.
- Adjusted versus unadjusted price semantics remain unverified.
- Corporate-action and delisting completeness remain unverified.
- A pilot audit demonstrates the pipeline only for its stated symbols and range; it does not imply
  full HOSE historical coverage.

Phase 2.3 builds full-universe resume and assembly on these immutable exact runs. See
[universe-ingestion-campaign.md](universe-ingestion-campaign.md).
