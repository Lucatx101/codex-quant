# CODEX PROMPT — PHASE 1
## Data Foundation: vnstock Storage, Normalization, Validation

You are working inside the existing `codex-quant` repository.

Phase 0 initialized the project, created the vnstock data capability audit, and pushed the repository to GitHub. The live audit verified daily OHLCV, intraday bars, latest quote, batch price board, and current exchange universe. It also exposed unresolved issues: quote timestamps are parsed misleadingly near 1970, corporate actions returned empty for FPT, adjusted-price semantics remain unknown, point-in-time universe data is not verified, and no free-tier WebSocket is available.

Your task in Phase 1 is to build the data foundation only.

Do not implement trading strategies, signal generation, backtesting, portfolio construction, machine learning, live trading, or the final web UI in this phase.

---

# 1. Phase 1 objectives

Implement a robust data layer for Vietnamese equities using vnstock as the initial provider.

The system must support:

1. small-universe daily OHLCV backfills;
2. small-universe intraday bar fetches where available;
3. batch latest-quote snapshots;
4. current HOSE universe snapshots;
5. immutable raw-data storage;
6. normalized Parquet datasets;
7. deterministic data-quality checks;
8. metadata manifests for reproducibility;
9. CLI commands for data operations;
10. offline tests using mocks;
11. explicit protection against secrets and generated market data being committed.

This phase prepares the data layer for future feature engineering and backtesting. It does not create features or strategies.

---

# 2. Inspect current repo state before editing

Run:

```bash
git status --short
git branch --show-current
git remote -v
git log -3 --oneline
find . -maxdepth 3 -type f | sort
```

Read:

- `AGENTS.md`
- `README.md`
- `pyproject.toml`
- `Makefile`
- `docs/architecture.md`
- `docs/data-capability-report.md`
- `reports/data_capabilities.json`
- existing files under `src/`
- existing tests

Preserve existing work unless a correction is required.

Do not rewrite Git history.
Do not force-push.

---

# 3. Mandatory Phase 0 audit cleanup

The live audit report shows verified capabilities, but it may still contain stale wording claiming no live API-key-backed requests were completed.

Fix the audit/report-generation code so that:

1. if at least one live request was executed with `VNSTOCK_API_KEY` set, the report must not claim that no live API-key-backed requests were completed;
2. unresolved uncertainties should only contain true unresolved issues;
3. blocking issues should be accurate;
4. Markdown and JSON reports remain generated from the same structured result;
5. tests cover this case.

Also investigate the quote/price-board timestamp issue:

- Phase 0 live audit rendered quote timestamps near `1970-01-01`.
- This likely means the `time` field is not being parsed with the correct unit or semantics.
- Do not guess silently.
- Inspect raw value type and provider documentation.
- Implement a safe parser or mark the timestamp as provider-specific/unparsed.
- Add tests preventing misleading timestamp conversion.

If timestamp semantics cannot be verified, normalized quote snapshots must preserve `provider_time_raw` and set parsed time/status explicitly to unknown/unparsed.

---

# 4. Scope boundaries

Allowed in Phase 1:

- data storage layout;
- provider adapter improvements;
- CLI data commands;
- raw and normalized Parquet writing;
- schema validation;
- manifest files;
- data-quality reports;
- rate-limit guardrails;
- unit tests and integration tests with mocks;
- documentation updates;
- optional live smoke commands, skipped by default.

Forbidden in Phase 1:

- trading strategies;
- technical indicators used as signals;
- alpha models;
- HMM, ML, XGBoost, LSTM, transformers;
- backtesting engine;
- position sizing;
- portfolio optimization;
- broker integration;
- order execution;
- final browser UI;
- React, Next.js, Vue;
- Flask, FastAPI, Django;
- dashboards;
- charts.

---

# 5. Data directories and Git policy

Use this storage policy:

```text
data/
├── raw/
│   └── vnstock/
├── normalized/
│   └── vnstock/
├── cache/
└── manifests/
```

Generated market data must not be committed.

Update `.gitignore` so Git ignores generated files under:

```text
data/raw/**
data/normalized/**
data/cache/**
data/manifests/**
reports/data_quality/**
```

but preserves directory structure with `.gitkeep` files where useful.

Do not commit generated market data.

Do commit:

- source code;
- tests;
- docs;
- prompt file;
- `.gitignore`;
- `.env.example`;
- small static test fixtures only if sanitized and stable.

---

# 6. Data model requirements

Create typed models or schemas for the following logical datasets.

## 6.1 Universe snapshot

Fields should include, when available:

- provider;
- exchange;
- symbol;
- organ_name;
- english_organ_name;
- security_type;
- provider_id;
- snapshot_timestamp_utc;
- raw_exchange_field;
- raw_type_field.

Do not assume the provider output contains only HOSE common stocks.

Implement explicit filtering and diagnostics:

- count total returned rows;
- count rows with exchange == HOSE;
- count rows with null exchange;
- count rows by security type;
- count duplicate symbols;
- preserve unknown types rather than dropping silently.

## 6.2 Daily OHLCV

Fields should include:

- provider;
- symbol;
- exchange, nullable;
- date;
- open;
- high;
- low;
- close;
- volume;
- adjusted_flag, nullable or unknown;
- source_resolution;
- ingestion_timestamp_utc.

Validation:

- required OHLCV fields exist;
- prices are numeric and non-negative;
- high >= low;
- high >= open/close where data is valid;
- low <= open/close where data is valid;
- volume is integer-like and non-negative;
- no duplicate `(symbol, date)`;
- sorted by symbol/date;
- no future dates beyond local market calendar tolerance;
- timezone handling is explicit.

## 6.3 Intraday bars

Fields should include:

- provider;
- symbol;
- exchange, nullable;
- timestamp;
- trading_date;
- resolution;
- open;
- high;
- low;
- close;
- volume;
- volume_semantics: `per_bar`, `cumulative`, or `unknown`;
- bar_status: `complete`, `forming`, or `unknown`;
- ingestion_timestamp_utc.

Validation:

- required columns exist;
- timestamp parse behavior explicit;
- no duplicate `(symbol, resolution, timestamp)`;
- sorted by symbol/resolution/timestamp;
- no negative price or volume;
- identify session-break gaps but do not treat them as errors;
- record whether timestamps are timezone-naive.

## 6.4 Quote snapshot

Fields should include:

- provider;
- symbol;
- snapshot_timestamp_utc;
- provider_time_raw;
- provider_time_parsed, nullable;
- provider_time_parse_status;
- exchange;
- reference_price;
- ceiling_price;
- floor_price;
- open_price;
- high_price;
- low_price;
- close_price;
- average_price;
- volume_accumulated;
- total_value;
- price_change;
- percent_change;
- bid_price_1, bid_vol_1;
- bid_price_2, bid_vol_2;
- bid_price_3, bid_vol_3;
- ask_price_1, ask_vol_1;
- ask_price_2, ask_vol_2;
- ask_price_3, ask_vol_3;
- foreign_buy_volume, nullable;
- foreign_sell_volume, nullable;
- foreign_room, nullable.

Validation:

- one row per requested symbol where provider returns data;
- missing symbols reported;
- raw provider time preserved;
- parsed provider time not misleading;
- price fields numeric where present;
- volume fields non-negative.

---

# 7. Storage format

Use Parquet for normalized datasets.

Use simple partitioning:

```text
data/normalized/vnstock/universe/snapshot_date=YYYY-MM-DD/*.parquet
data/normalized/vnstock/daily/symbol=FPT/*.parquet
data/normalized/vnstock/intraday/resolution=1m/symbol=FPT/trading_date=YYYY-MM-DD/*.parquet
data/normalized/vnstock/quotes/snapshot_date=YYYY-MM-DD/*.parquet
```

Raw storage should preserve provider outputs without credentials:

```text
data/raw/vnstock/<dataset>/<run_id>/...
```

Raw files may use JSONL, CSV, or Parquet depending on response shape.

Every run should create a manifest under:

```text
data/manifests/<run_id>.json
```

The manifest should include:

- run_id;
- command;
- provider;
- symbols;
- exchange;
- start/end date;
- resolution;
- started_at_utc;
- finished_at_utc;
- status;
- row counts;
- output paths;
- validation summary;
- error summary;
- package versions;
- Git commit hash if available.

Never store API keys, request auth headers, or secret-bearing metadata.

---

# 8. CLI requirements

Extend the CLI with data subcommands:

```bash
python -m hose_quant.cli data fetch-universe --exchange HOSE
python -m hose_quant.cli data backfill-daily --symbols FPT,HPG,VCB --start 2025-01-01 --end 2026-07-03
python -m hose_quant.cli data fetch-intraday --symbols FPT --resolution 1m --lookback-days 1
python -m hose_quant.cli data snapshot-quotes --symbols FPT,HPG,VCB
python -m hose_quant.cli data validate
```

CLI behavior:

- useful help text;
- non-zero exit on failure;
- clear error messages;
- no secret leakage;
- bounded provider calls;
- no full-HOSE fetch unless explicitly requested with a safety flag;
- dry-run support where practical;
- live provider calls require `VNSTOCK_API_KEY`;
- tests do not require live credentials.

Add Makefile targets where useful:

```bash
make data-universe
make data-daily-smoke
make data-quotes-smoke
```

These live smoke targets may require local credentials and must be documented as optional.

Do not make `make check` depend on live data.

---

# 9. Rate-limit and request safety

Implement conservative provider-call guardrails.

Requirements:

- default max symbols for quote snapshot should be small, e.g. 20;
- any command requesting more than the safe default must require explicit override such as `--allow-large-universe`;
- batch quotes should use documented batch method where possible;
- avoid per-symbol loops for quote snapshots when batch is available;
- configurable sleep/backoff;
- retry only retryable provider/network errors;
- do not retry invalid symbol or auth errors;
- log provider call counts;
- record call counts in manifests.

Do not deliberately test or exhaust the free-tier limit.

---

# 10. Validation and data-quality reporting

Implement a validation module returning structured validation results.

Each validation result should include:

- dataset name;
- severity: `INFO`, `WARNING`, `ERROR`;
- check name;
- message;
- affected columns;
- affected row count;
- sample affected keys where safe;
- whether the issue blocks normalized output.

Generate data-quality summary reports:

```text
reports/data_quality/latest.md
reports/data_quality/latest.json
```

These generated reports should generally be ignored by Git unless deliberately committed as sanitized documentation samples.

For Phase 1, commit source/tests/docs, not generated live data-quality output.

---

# 11. Provider adapter requirements

Refactor or extend `vnstock_adapter.py` carefully.

Requirements:

- no guessed endpoints;
- no undocumented WebSocket;
- no silent fallback across providers;
- clear exceptions;
- sanitized errors;
- typed returned DataFrames or typed records;
- small well-named methods;
- no business logic inside provider adapter;
- no trading interpretation inside provider adapter.

Suggested adapter methods:

```python
fetch_universe(exchange: str) -> pd.DataFrame
fetch_daily_ohlcv(symbol: str, start: date, end: date) -> pd.DataFrame
fetch_intraday_bars(symbol: str, resolution: str, lookback_days: int) -> pd.DataFrame
fetch_quote_snapshot(symbols: list[str]) -> pd.DataFrame
```

Methods should return provider-shaped or minimally cleaned data.

Normalization belongs in separate modules.

---

# 12. Recommended source layout

Adapt existing structure toward:

```text
src/hose_quant/
├── cli.py
├── config.py
├── logging.py
├── data/
│   ├── __init__.py
│   ├── base.py
│   ├── models.py
│   ├── vnstock_adapter.py
│   ├── normalizers.py
│   ├── storage.py
│   ├── validators.py
│   ├── manifests.py
│   └── workflows.py
└── reporting/
    ├── __init__.py
    └── markdown.py
```

Keep modules cohesive and small.

Avoid huge files.

---

# 13. Tests

All default tests must run offline.

Use mocks/fakes for vnstock behavior.

Add or update tests for:

- CLI parsing and exit codes;
- missing `VNSTOCK_API_KEY` for live commands;
- secret redaction;
- universe normalization with null exchange fields;
- filtering/diagnostics for HOSE universe;
- daily OHLCV normalization;
- daily duplicate detection;
- daily unsorted data handling;
- daily invalid OHLC relationship;
- intraday timestamp handling;
- intraday duplicate detection;
- quote snapshot normalization;
- quote raw provider time preservation;
- quote parsed timestamp not becoming misleading 1970 dates;
- missing symbols in quote snapshot;
- manifest creation;
- storage path generation;
- generated market data ignored by default;
- Markdown/JSON data-quality report generation;
- Phase 0 audit stale wording fix.

Do not require live network in tests.

---

# 14. Documentation updates

Update:

- `README.md`
- `docs/architecture.md`
- `AGENTS.md` only if a concise project rule is needed
- add `docs/data-foundation.md`
- keep this prompt under `docs/prompts/phase-1-data-foundation-prompt.md`

`docs/data-foundation.md` should explain:

- storage layout;
- datasets;
- normalization rules;
- validation rules;
- CLI commands;
- live-data safety limits;
- unresolved issues;
- what Phase 2 should build next.

Mention clearly:

- no strategy exists yet;
- no backtester exists yet;
- no web UI exists yet;
- generated market data is intentionally ignored by Git.

---

# 15. Quality gates

Before completion, run:

```bash
make lint
make typecheck
make test
make check
```

Run safe CLI dry-runs or offline commands where available.

If you run live data commands, use only a small symbol set such as:

```text
FPT,HPG,VCB
```

and never commit generated market data.

Before committing, check:

```bash
git status --short
git diff --stat
git diff -- .gitignore README.md docs src tests Makefile pyproject.toml
```

Run a local secret scan using reasonable patterns, without printing secret values.

---

# 16. Git commit and push

After all required checks pass:

1. stage relevant source, tests, docs, config, and prompt files;
2. ensure generated data is not staged;
3. commit with:

```text
feat: add vnstock data foundation
```

4. push to `origin main`.

Do not commit if required checks fail.
Do not force-push.

---

# 17. Completion report

At the end, provide a concise report containing:

1. files created or modified;
2. data commands implemented;
3. storage layout implemented;
4. validation checks implemented;
5. manifest behavior;
6. Phase 0 audit/report bug fixes;
7. tests added;
8. lint result;
9. type-check result;
10. test result;
11. `make check` result;
12. live commands run, if any;
13. generated data paths, if any;
14. confirmation that generated data was not committed;
15. Git commit hash;
16. push status;
17. unresolved blockers;
18. recommended Phase 2 scope.

Also output:

```bash
git status --short
git log -1 --oneline
git remote -v
```

Stop after Phase 1.
