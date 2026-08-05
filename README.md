# hose-quant-system

`hose-quant-system` is a production-grade quantitative trading project for Vietnamese equities listed on HOSE. The intended holding horizon is approximately T+2 to T+20.

## Current Scope

Phase 0 bootstrapped the repository and implemented a reproducible `vnstock` capability audit.

Phase 1 adds the provider data foundation. Phase 2 adds a local-only feature-input layer, and
Phase 2.2 hardens daily re-ingestion and coverage auditing. Phase 2.3 adds a resumable,
universe-scale ingestion campaign and safe dataset assembly. Phase 2.3.1 separates structural
assembly from explicit research-readiness acceptance:

- small-universe daily OHLCV backfills;
- small-universe intraday fetches;
- batch quote snapshots;
- current HOSE universe snapshots;
- immutable raw storage;
- normalized Parquet datasets;
- validation results and manifests;
- auditable current-snapshot research universes;
- configurable backward-looking liquidity characterization;
- typed long-form daily panels;
- per-symbol availability diagnostics;
- rate-aware, date-chunked daily re-ingestion with all-or-nothing normalized publication;
- exact-run HOSE coverage, quality, staleness, and unit-provenance audits;
- immutable full-universe campaign plans and per-task attempt receipts;
- provider-limited batches with interruption recovery and explicit retry controls;
- campaign-level virtual coverage and compatibility audits;
- deterministic, atomic, versioned assembly with row-level source lineage;
- versioned campaign-level coverage-quality policy and readiness evidence;
- canonical candidacy only after an accepted matching audit and assembly;
- explicit market-time, adjustment, unit, and point-in-time uncertainty.

No strategy, signal, label, backtesting, machine-learning, live trading, portfolio construction,
or browser UI functionality exists yet.

## Environment

- macOS
- Python 3.11+
- Git and GitHub
- VS Code
- `vnstock` as the initial market-data provider

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
```

Configure credentials without committing them:

```bash
cp .env.example .env
```

Edit `.env` locally and set `VNSTOCK_API_KEY`. The real `.env` file is ignored by Git.

## Commands

```bash
make install
make lint
make typecheck
make test
make check
make audit
make data-universe
make data-daily-smoke
make data-quotes-smoke
```

`make check` runs only offline quality gates. `make audit` performs the live provider audit and requires `VNSTOCK_API_KEY`.
The `make data-*` targets are optional live smoke commands and require `VNSTOCK_API_KEY`.

For an offline, unverified report-generation run:

```bash
PYTHONPATH=src python3 -m hose_quant.cli audit-data --offline
```

Provider data commands:

```bash
python3 -m hose_quant.cli data fetch-universe --exchange HOSE
python3 -m hose_quant.cli data backfill-daily --symbols FPT,HPG,VCB --start 2020-01-01 --end 2026-07-03 --chunk-calendar-days 730
python3 -m hose_quant.cli data fetch-intraday --symbols FPT --resolution 1m --lookback-days 1
python3 -m hose_quant.cli data snapshot-quotes --symbols FPT,HPG,VCB
python3 -m hose_quant.cli data validate
```

Use `--dry-run` on data fetch commands to verify command handling without provider calls.

Feature-input commands use only local normalized Parquet and never require credentials:

```bash
python3 -m hose_quant.cli data prepare-universe --snapshot-date 2026-07-04
python3 -m hose_quant.cli data prepare-universe --snapshot-date 2026-07-04 --with-liquidity --liquidity-reference-date 2026-07-02
python3 -m hose_quant.cli data build-daily-panel --symbols FPT,HPG,VCB --start 2026-05-04 --end 2026-07-02
python3 -m hose_quant.cli data audit-daily-coverage --daily-run-id RUN_ID --start 2020-01-01 --end 2026-07-03 --snapshot-date 2026-07-04
python3 -m hose_quant.cli data init-daily-campaign --campaign-id hose-daily-20260805 --snapshot-date 2026-08-05 --start 2020-01-01 --end 2026-08-04 --chunk-calendar-days 730
python3 -m hose_quant.cli data adopt-daily-run --campaign-id hose-daily-20260805 --daily-run-id RUN_ID
python3 -m hose_quant.cli data run-daily-campaign --campaign-id hose-daily-20260805 --max-tasks 20 --dry-run
python3 -m hose_quant.cli data audit-daily-campaign --campaign-id hose-daily-20260805
python3 -m hose_quant.cli data assemble-daily-campaign --campaign-id hose-daily-20260805
```

See [docs/feature-input-layer.md](docs/feature-input-layer.md) for contracts, unit provenance,
point-in-time limitations, and screening options. See
[docs/reingestion-coverage-audit.md](docs/reingestion-coverage-audit.md) for the Phase 2.2
re-ingestion and audit runbook. See
[docs/universe-ingestion-campaign.md](docs/universe-ingestion-campaign.md) for campaign resume,
adoption, audit, compatibility, and assembly rules.

## Reports

- JSON: `reports/data_capabilities.json`
- Markdown: `docs/data-capability-report.md`
- Data-quality reports: `reports/data_quality/latest.json` and `reports/data_quality/latest.md` when `data validate` is run
- Feature-input diagnostics: `reports/feature_inputs/` when a daily panel is built
- Daily coverage audits: `reports/data_quality/*-daily-coverage.{json,md}`
- Campaign audits: `reports/data_quality/campaigns/<campaign-id>/`

Reports must not contain credentials, raw auth headers, or generated market data.

## Data Storage

Generated market data is intentionally ignored by Git:

- raw provider data: `data/raw/vnstock/`
- normalized Parquet: `data/normalized/vnstock/`
- feature-input Parquet: `data/feature_inputs/vnstock/`
- campaign plans, state, receipts, and audits: `data/campaigns/vnstock/daily/`
- assembled versioned daily datasets: `data/assembled/vnstock/daily/`
- manifests: `data/manifests/`
- caches: `data/cache/`

## Git Workflow

Work on `main` unless a later phase defines a branch workflow. Before committing, run `make check`, inspect staged changes, and confirm no secrets or generated market data are staged.

## Known Limitations

- Free-tier minute polling practicality and full quote batch limits are not yet proven.
- Historical point-in-time universe membership, delisting state, adjusted-price methodology, and corporate-action completeness are not yet verified.
- Legacy normalized daily data remains non-monetary because it lacks source-specific unit
  provenance. New KBS daily rows can enable VND traded value only when every selected row carries
  the matching registered, versioned provenance record; no CLI flag can assert verification.
- Quote provider `time` values are preserved as raw provider-specific values unless documentation verifies timestamp semantics.

## Future Browser Interface

The final completed product will expose its functions through a browser-based interface rendered as HTML. The UI stack will be selected later. Trading and data logic must remain outside the UI.
