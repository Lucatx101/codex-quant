# hose-quant-system

`hose-quant-system` is a production-grade quantitative trading project for Vietnamese equities listed on HOSE. The intended holding horizon is approximately T+2 to T+20.

## Current Scope

Phase 0 bootstrapped the repository and implemented a reproducible `vnstock` capability audit.

Phase 1 adds the data foundation only:

- small-universe daily OHLCV backfills;
- small-universe intraday fetches;
- batch quote snapshots;
- current HOSE universe snapshots;
- immutable raw storage;
- normalized Parquet datasets;
- validation results and manifests.

No strategy, backtesting, machine-learning, live trading, portfolio construction, or browser UI functionality exists yet.

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
python -m pip install --upgrade pip
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
PYTHONPATH=src python -m hose_quant.cli audit-data --offline
```

Phase 1 data commands:

```bash
python -m hose_quant.cli data fetch-universe --exchange HOSE
python -m hose_quant.cli data backfill-daily --symbols FPT,HPG,VCB --start 2025-01-01 --end 2026-07-03
python -m hose_quant.cli data fetch-intraday --symbols FPT --resolution 1m --lookback-days 1
python -m hose_quant.cli data snapshot-quotes --symbols FPT,HPG,VCB
python -m hose_quant.cli data validate
```

Use `--dry-run` on data fetch commands to verify command handling without provider calls.

## Reports

- JSON: `reports/data_capabilities.json`
- Markdown: `docs/data-capability-report.md`
- Data-quality reports: `reports/data_quality/latest.json` and `reports/data_quality/latest.md` when `data validate` is run

Reports must not contain credentials, raw auth headers, or generated market data.

## Data Storage

Generated market data is intentionally ignored by Git:

- raw provider data: `data/raw/vnstock/`
- normalized Parquet: `data/normalized/vnstock/`
- manifests: `data/manifests/`
- caches: `data/cache/`

## Git Workflow

Work on `main` unless a later phase defines a branch workflow. Before committing, run `make check`, inspect staged changes, and confirm no secrets or generated market data are staged.

## Known Limitations

- Free-tier minute polling practicality and full quote batch limits are not yet proven.
- Historical point-in-time universe membership, delisting state, adjusted-price methodology, and corporate-action completeness are not yet verified.
- Quote provider `time` values are preserved as raw provider-specific values unless documentation verifies timestamp semantics.

## Future Browser Interface

The final completed product will expose its functions through a browser-based interface rendered as HTML. The UI stack will be selected later. Trading and data logic must remain outside the UI.
