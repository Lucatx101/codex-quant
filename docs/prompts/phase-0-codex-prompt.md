# CODEX PROMPT — PHASE 0  
## Repository Bootstrap and vnstock Data Capability Audit

You are starting a new production-grade quantitative trading project for Vietnamese equities listed on HOSE.

The intended trading horizon is approximately T+2 to T+20. The initial market-data provider is vnstock/vnstocks.

This phase is strictly limited to:

1. bootstrapping the repository;
2. establishing engineering standards;
3. inspecting the actual capabilities of the installed/current vnstock package and the user's free API tier;
4. implementing a small, safe, reproducible data-capability audit;
5. documenting verified findings;
6. committing the completed Phase 0 work to Git;
7. pushing to the configured Git remote when possible.

Do not implement trading strategies, backtesting logic, machine-learning models, live trading, portfolio construction, or the final web interface in this phase.

---

# 1. Project identity

Use the project name:

```text
hose-quant-system
```

Primary environment:

- macOS;
- Python 3.11+;
- VS Code;
- Git and GitHub;
- HOSE equities;
- vnstock as the initial market-data provider.

The final completed product will later expose its functions through a browser-based web interface rendered as HTML.

That final web interface is a future application layer. Do not implement it in Phase 0.

Phase 0 reports may use Markdown and JSON. HTML reports are not required.

---

# 2. Core engineering principles

Apply these principles throughout the project:

- production-grade modular architecture;
- point-in-time correctness;
- no look-ahead bias;
- no survivorship assumptions;
- explicit handling of missing or unavailable data;
- reproducible environments;
- deterministic tests where applicable;
- no business logic inside notebooks;
- no silent exception handling;
- no fabricated or guessed API methods;
- no unsupported claims about provider capabilities;
- no strategy implementation in Phase 0;
- no unnecessary infrastructure or premature optimization.

When a capability cannot be verified, mark it as unverified or unavailable. Never simulate success.

---

# 3. Inspect the workspace before editing

Before creating or modifying files:

1. inspect the current directory;
2. inspect any existing Git repository;
3. inspect any existing `AGENTS.md`, `README.md`, `pyproject.toml`, or source tree;
4. preserve valid existing work;
5. do not overwrite user files unnecessarily;
6. report any conflicting existing structure before making destructive changes.

If the workspace is empty, initialize it using the project structure defined below.

---

# 4. Git and GitHub requirements

Git is a permanent requirement for the project.

## 4.1 Repository initialization

1. Initialize a Git repository if one does not already exist.
2. Use `main` as the default branch.
3. Create a comprehensive `.gitignore`.
4. Preserve an existing valid remote.
5. Do not rewrite Git history.
6. Do not force-push.

## 4.2 Files that must never be committed

Never commit:

- API keys;
- `.env`;
- authentication tokens;
- credentials;
- private certificates;
- temporary API responses containing sensitive metadata;
- raw authentication headers;
- Python caches;
- test caches;
- virtual environments;
- IDE-local state;
- temporary logs;
- local databases not intended for version control;
- generated market-data files;
- generated reports that contain secrets.

Keep empty data directories in Git only through `.gitkeep` when needed.

## 4.3 Remote repository and push behavior

At the end of Phase 0:

1. run all required validation commands;
2. commit only after all mandatory checks pass;
3. use a commit message similar to:

```text
chore: initialize project and audit vnstock capabilities
```

4. if a Git remote already exists, push the `main` branch;
5. if no remote exists:
   - do not invent a GitHub account or repository URL;
   - do not choose public/private visibility without user instruction;
   - report that the local repository is ready;
   - provide the exact commands needed to add the remote and push;
6. if GitHub CLI is authenticated and repository visibility is already specified by existing project configuration, creation may proceed;
7. otherwise stop before remote creation.

Do not commit or push if lint, type-checking, or tests fail.

---

# 5. API-key handling

The user has a free vnstock API key.

The key must be supplied through the environment variable:

```text
VNSTOCK_API_KEY
```

Never hardcode or echo the key.

Create:

```text
.env.example
```

with exactly a placeholder such as:

```dotenv
VNSTOCK_API_KEY=replace_with_your_key
APP_ENV=development
LOG_LEVEL=INFO
```

The actual `.env` file must be ignored by Git.

Requirements:

- use typed settings;
- fail with a clear message when an authenticated command is executed without the key;
- never include the key in console output;
- never include the key in logs;
- never include the key in tracebacks;
- never include the key in generated Markdown or JSON reports;
- redact secret values from configuration representations;
- do not persist request headers containing credentials.

Prefer `pydantic-settings` or an equivalent typed configuration approach.

Use `python-dotenv` only if needed.

---

# 6. Official vnstock documentation and Agent Guide

Before implementing the vnstock adapter:

1. inspect the current official vnstock documentation;
2. inspect the installed package;
3. inspect the current official vnstock Agent Guide or Codex instructions if available;
4. verify package names, versions, providers, methods, and authentication behavior;
5. use only documented or executable package methods;
6. do not infer unsupported methods from names seen in outdated examples;
7. do not copy an entire external documentation repository into this project;
8. do not add large documentation dumps to `AGENTS.md`;
9. record the source and date of important documentation findings.

The project must record:

- installed package name;
- installed package version;
- Python compatibility;
- supported providers found;
- authentication mechanism;
- documented free-tier restrictions;
- documented rate limit, if available;
- whether the price board supports batches;
- whether intraday data is historical, delayed, polling-based, or streaming;
- whether minute data is accessible with the free tier;
- whether WebSocket or another streaming mechanism is available;
- whether WebSocket access requires a paid or sponsored tier;
- any uncertainty that remains.

If internet access is unavailable:

- inspect the installed package;
- inspect package metadata and callable signatures;
- run only safe executable checks;
- state explicitly that online documentation could not be verified;
- do not claim current provider behavior from memory.

---

# 7. Proposed repository structure

Use a clean `src/` layout.

Create or adapt toward:

```text
hose-quant-system/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── Makefile
├── configs/
│   └── app.example.yaml
├── data/
│   ├── raw/
│   │   └── .gitkeep
│   ├── normalized/
│   │   └── .gitkeep
│   └── cache/
│       └── .gitkeep
├── docs/
│   ├── architecture.md
│   └── data-capability-report.md
├── reports/
│   └── data_capabilities.json
├── scripts/
│   └── run_data_audit.py
├── src/
│   └── hose_quant/
│       ├── __init__.py
│       ├── cli.py
│       ├── config.py
│       ├── logging.py
│       └── data/
│           ├── __init__.py
│           ├── base.py
│           ├── models.py
│           └── vnstock_adapter.py
└── tests/
    ├── test_cli.py
    ├── test_config.py
    ├── test_data_models.py
    ├── test_report_generation.py
    └── test_vnstock_adapter.py
```

You may improve this structure when justified, but keep it simple.

Do not add a web framework in Phase 0.

Do not create:

- React;
- Next.js;
- Vue;
- Flask;
- FastAPI;
- Django;
- HTMX;
- frontend build tooling.

The future HTML interface must be described in the architecture document only.

---

# 8. Dependency management

Use `pyproject.toml`.

Use modern, maintained dependencies only.

Likely dependencies include:

- the current official vnstock package;
- pandas;
- pyarrow, only if Parquet support is required in Phase 0;
- pydantic;
- pydantic-settings;
- pytest;
- pytest-cov;
- ruff;
- mypy;
- tenacity, only if bounded retries are implemented;
- packaging or importlib metadata for version inspection.

Do not add:

- Redis;
- Kafka;
- Airflow;
- Celery;
- databases;
- machine-learning libraries;
- web frameworks;
- plotting libraries;
- notebook dependencies.

Pin or constrain versions sensibly without using arbitrary stale versions.

---

# 9. Development commands

Create a Makefile or equivalent commands for:

```bash
make install
make lint
make typecheck
make test
make audit
make check
```

Expected behavior:

- `make lint`: run Ruff checks;
- `make typecheck`: run mypy;
- `make test`: run offline tests;
- `make audit`: run the real data-capability audit;
- `make check`: run all mandatory offline quality gates.

Do not make `make check` depend on live network access or a real API key.

Live tests and live audits must be explicit opt-in operations.

---

# 10. Typed configuration

Implement typed application settings.

At minimum support:

- `VNSTOCK_API_KEY`;
- application environment;
- log level;
- data directory;
- report directory;
- request timeout;
- maximum retry attempts;
- provider selection when supported.

Requirements:

- secrets must be represented with secret-aware types;
- configuration `repr` must not expose credentials;
- errors must be clear and actionable;
- directories should resolve consistently from the project root;
- no implicit dependence on the current shell working directory.

---

# 11. Logging

Implement readable structured logging.

Requirements:

- timestamp;
- log level;
- module name;
- concise message;
- useful error context;
- no credential leakage;
- no raw authentication headers;
- no full raw API response body by default;
- no duplicate handlers;
- no silent exception suppression.

Add a reusable redaction mechanism for secrets and sensitive headers.

---

# 12. Data-provider abstraction

Define a minimal provider interface, protocol, or abstract base class.

Do not over-design it.

The abstraction should support capability discovery without claiming that unsupported functions exist.

Potential capabilities include:

- daily historical OHLCV;
- historical intraday bars;
- latest quote;
- batch price board;
- symbol metadata;
- current exchange universe;
- listing date;
- delisting status;
- corporate actions;
- adjusted prices;
- foreign trading;
- proprietary trading;
- order-book depth;
- streaming or WebSocket.

Each capability must be classified using explicit statuses such as:

- `VERIFIED`;
- `DOCUMENTED_NOT_TESTED`;
- `UNAVAILABLE_FREE_TIER`;
- `UNAVAILABLE_PACKAGE`;
- `AUTHENTICATION_REQUIRED`;
- `RATE_LIMITED`;
- `NETWORK_ERROR`;
- `PROVIDER_ERROR`;
- `INVALID_SCHEMA`;
- `EMPTY_RESPONSE`;
- `UNKNOWN`.

Do not mark a capability as supported merely because a method with a similar name exists.

---

# 13. Typed capability-result model

Represent every audited capability with a typed model.

Include fields similar to:

- capability name;
- status;
- provider;
- authentication tier;
- package name;
- package version;
- library method or documented endpoint;
- tested symbols;
- request timestamp;
- elapsed latency;
- returned row count;
- earliest timestamp;
- latest timestamp;
- timezone information;
- schema summary;
- data-quality findings;
- limitations;
- error category;
- sanitized error message;
- evidence notes.

Use enums for statuses and error categories.

Serialize audit results to:

```text
reports/data_capabilities.json
```

The JSON output must:

- be valid UTF-8;
- be deterministic where practical;
- contain no secrets;
- contain no authentication headers;
- contain no fabricated results.

---

# 14. Executable data audit

Create an executable command such as:

```bash
python -m hose_quant.cli audit-data
```

and optionally keep:

```bash
python scripts/run_data_audit.py
```

as a thin wrapper.

The audit must:

- inspect the installed package;
- record package metadata;
- make a small number of respectful live requests only when credentials and network access are available;
- use safe timeouts;
- use bounded retries only for retryable failures;
- avoid exhausting the free quota;
- classify all failures accurately;
- write both JSON and Markdown outputs from the same structured result.

Use a small test set such as:

```text
VNINDEX
FPT
HPG
VCB
```

Adapt symbols only when required by the actual provider API.

Do not scan the full HOSE market in Phase 0.

---

# 15. Audit scope

Test only capabilities actually exposed by the installed/documented provider.

## 15.1 Daily OHLCV

Where available, inspect:

- columns and schema;
- date or datetime type;
- timezone handling;
- adjusted versus unadjusted prices;
- null values;
- duplicate dates;
- ascending or descending sort order;
- numeric types;
- earliest and latest returned date;
- handling of invalid symbols.

## 15.2 Intraday data

Where available, inspect:

- supported intervals;
- supported lookback;
- maximum row count;
- earliest and latest timestamp;
- timezone;
- session breaks;
- whether volume is per bar or cumulative;
- whether values are delayed;
- pagination behavior;
- whether the latest bar appears complete or still forming;
- practical suitability for minute polling.

Do not claim “real time” unless the test and documentation support that claim.

Use terms such as:

- real-time;
- near-real-time;
- delayed;
- polling;
- historical intraday;
- unknown;

only when justified.

## 15.3 Current quote or price board

Where available, inspect:

- whether one request can return multiple symbols;
- number of symbols supported per request;
- reference, ceiling, and floor prices;
- current or latest matched price;
- cumulative volume and value;
- trading status;
- bid and ask levels;
- timestamp fields;
- observed response latency;
- whether data is appropriate for repeated polling.

## 15.4 Streaming or WebSocket

Inspect only documented interfaces.

Determine:

- whether a documented streaming interface exists;
- whether it is included in the free tier;
- whether additional authentication is required;
- whether it is part of the installed package;
- whether it can be safely tested.

Do not probe private, undocumented, or guessed endpoints.

## 15.5 Symbols and market universe

Where available, inspect:

- current HOSE symbols;
- exchange field;
- security type;
- listing status;
- listing date;
- delisting information;
- sector classification;
- duplicate symbols;
- missing metadata.

Explicitly note whether historical point-in-time universe membership is available.

## 15.6 Corporate actions and adjusted prices

Where available, inspect:

- cash dividends;
- stock dividends;
- splits;
- bonus shares;
- rights issues;
- ex-date;
- record date;
- adjusted historical prices;
- data completeness.

Explicitly note whether corporate-action data is sufficient to reconstruct adjusted histories without look-ahead bias.

## 15.7 Rate limits

Use documented limits where available.

Do not deliberately trigger or exhaust the quota.

Record:

- documented request limit;
- observed rate-limit headers, if naturally returned;
- naturally encountered rate-limit errors;
- retry-after metadata, if available;
- implications for a 20-, 50-, 100-, and full-HOSE-symbol universe.

Make conservative estimates and clearly label them as estimates.

---

# 16. Network and retry behavior

Every live network operation must use:

- a finite timeout;
- bounded retries;
- exponential backoff only for retryable errors;
- no retries for invalid credentials, invalid requests, or invalid symbols unless documented;
- explicit exception mapping;
- sanitized errors;
- clear exit codes.

Avoid retry storms.

The audit must remain safe for a free-tier account.

---

# 17. Markdown capability report

Generate:

```text
docs/data-capability-report.md
```

The Markdown report must be generated from the same structured audit result used for JSON.

It must include:

1. execution timestamp;
2. operating-system and Python information;
3. installed package name and version;
4. authentication state without exposing the key;
5. documented free-tier constraints;
6. capability summary;
7. detailed findings;
8. tested symbols;
9. schema and timestamp findings;
10. latency observations;
11. data-quality problems;
12. rate-limit implications;
13. unresolved uncertainties;
14. blocking issues;
15. recommended data architecture for Phase 1.

Include explicit conclusions for:

- whether daily OHLCV is usable;
- whether historical minute data is usable;
- whether free-tier minute polling is practical;
- whether batch price-board polling is practical;
- whether WebSocket is available to the free tier;
- recommended maximum initial live universe;
- recommended polling frequency;
- whether an alternative or paid data source may eventually be needed.

Do not add an HTML report in Phase 0.

---

# 18. Architecture document

Create:

```text
docs/architecture.md
```

Describe the planned future layers:

1. provider adapters;
2. immutable raw-data storage;
3. normalized point-in-time market data;
4. universe and corporate-action handling;
5. feature engine;
6. signal engine;
7. event-driven backtester;
8. walk-forward validation;
9. paper-trading and execution layer;
10. application service;
11. browser-based HTML user interface.

Mark layers 2–11 as planned or future work unless actually implemented in Phase 0.

For the future browser interface, state only the architectural intention:

- the final tool will be usable through a web browser;
- the interface will render HTML;
- the UI technology will be selected later;
- strategy and data logic must remain independent of the UI;
- the UI must call stable application services rather than contain trading logic.

Do not choose or implement FastAPI, Flask, Django, React, Next.js, HTMX, Plotly, or another UI stack in Phase 0.

---

# 19. Root AGENTS.md

Create a concise project-specific `AGENTS.md`.

Include rules for future Codex sessions:

- inspect repository state before editing;
- read relevant documentation before changing provider code;
- use official vnstock methods only;
- do not hallucinate endpoints or parameters;
- preserve point-in-time correctness;
- protect secrets;
- keep trading logic independent of the future UI;
- keep tests offline by default;
- update tests when behavior changes;
- update documentation when architecture or commands change;
- do not implement out-of-scope strategy logic;
- run `make check` before committing;
- do not commit failed experiments;
- do not commit generated market data;
- do not commit credentials;
- stop and report when a required capability cannot be verified.

Keep `AGENTS.md` concise enough to remain useful in Codex context.

---

# 20. README requirements

Create a practical `README.md` containing:

- project purpose;
- trading horizon context;
- explicit Phase 0 scope;
- current non-goals;
- environment setup;
- virtual-environment setup;
- API-key setup;
- installation command;
- audit command;
- lint command;
- type-check command;
- test command;
- full quality-check command;
- report locations;
- Git workflow;
- known limitations;
- future browser-based HTML interface requirement;
- clear statement that no strategy or live trading functionality exists yet.

Do not include the real API key.

---

# 21. Testing strategy

All default tests must run without live network access.

Use mocks or sanitized fixtures for provider tests.

Any live test must be:

- explicitly marked;
- skipped by default;
- enabled only through a deliberate environment flag or test marker;
- safe for the free-tier quota.

At minimum test:

- missing API-key handling;
- secret redaction;
- settings representation;
- capability-status serialization;
- provider error categorization;
- empty responses;
- malformed schemas;
- duplicate timestamps;
- unsorted timestamps;
- JSON report generation;
- Markdown report generation;
- deterministic report structure;
- no secret leakage;
- CLI success and failure exit codes;
- timeout mapping;
- non-retryable authentication failure behavior.

Do not record live API responses that contain credentials or unstable personal metadata.

---

# 22. Quality gates

Before completion, run:

```bash
make lint
make typecheck
make test
make check
```

Run:

```bash
make audit
```

only when network access and credentials are available.

If the real API key is not available inside the Codex shell:

- do not request it in source code;
- do not fake audit results;
- run all offline checks;
- verify the audit command's missing-key behavior;
- document the exact command the user should run locally;
- leave capability statuses unverified where appropriate.

If a live audit is successfully executed, regenerate:

```text
reports/data_capabilities.json
docs/data-capability-report.md
```

from the actual results.

---

# 23. Security checks

Before committing:

1. search the repository for patterns resembling the actual API-key prefix or secret variable value;
2. inspect staged changes;
3. confirm `.env` is ignored;
4. confirm generated reports contain no secrets;
5. confirm logs contain no secrets;
6. confirm test fixtures contain no secrets;
7. confirm Git history for this phase does not contain credentials.

Do not print the real secret during these checks.

---

# 24. Completion report

At the end, provide a concise completion report with:

1. repository path;
2. files created or modified;
3. installed vnstock package name and version;
4. documentation sources inspected;
5. audit commands executed;
6. capabilities verified;
7. capabilities unverified;
8. lint result;
9. type-check result;
10. test result;
11. `make check` result;
12. JSON report path;
13. Markdown report path;
14. Git branch;
15. commit hash;
16. Git remote;
17. push status;
18. exact unresolved blockers;
19. recommended Phase 1 scope.

Also output:

```bash
git status --short
git log -1 --oneline
git remote -v
```

Do not proceed to Phase 1.

Stop after Phase 0 is implemented, validated, committed, and pushed when possible.
