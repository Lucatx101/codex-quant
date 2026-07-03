# hose-quant-system

`hose-quant-system` is a production-grade quantitative trading project for Vietnamese equities listed on HOSE. The intended holding horizon is approximately T+2 to T+20.

## Phase 0 Scope

Phase 0 bootstraps the repository, establishes engineering standards, inspects the installed/current `vnstock` package, and implements a small reproducible data-capability audit.

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
```

`make check` runs only offline quality gates. `make audit` performs the live provider audit and requires `VNSTOCK_API_KEY`.

For an offline, unverified report-generation run:

```bash
PYTHONPATH=src python -m hose_quant.cli audit-data --offline
```

## Reports

- JSON: `reports/data_capabilities.json`
- Markdown: `docs/data-capability-report.md`

Reports must not contain credentials, raw auth headers, or generated market data.

## Git Workflow

Work on `main` unless a later phase defines a branch workflow. Before committing, run `make check`, inspect staged changes, and confirm no secrets or generated market data are staged.

## Known Limitations

- Live provider capabilities are unverified until `make audit` runs with a valid `VNSTOCK_API_KEY` and network access.
- Free-tier minute data, polling practicality, and batch price-board limits are not yet proven.
- Historical point-in-time universe membership, delisting state, adjusted-price methodology, and corporate-action completeness are not yet verified.

## Future Browser Interface

The final completed product will expose its functions through a browser-based interface rendered as HTML. The UI stack will be selected later. Trading and data logic must remain outside the UI.
