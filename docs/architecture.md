# Architecture

This project is a production-grade quantitative research and trading system for Vietnamese equities listed on HOSE. Phase 0 implements only repository standards and a provider capability audit. Strategy, backtesting, execution, portfolio construction, and the browser UI are future work.

## Planned Layers

1. Provider adapters: Phase 0 defines the first vnstock capability-audit adapter and keeps provider behavior behind a small interface.
2. Immutable raw-data storage: planned. Raw provider responses will be stored append-only with source, request, and retrieval metadata.
3. Normalized point-in-time market data: planned. Normalized tables must preserve timestamp, symbol, source, adjustment state, and universe membership validity.
4. Universe and corporate-action handling: planned. The system must avoid survivorship bias and must not apply future corporate actions to earlier decision points.
5. Feature engine: planned. Feature code will consume normalized point-in-time data only.
6. Signal engine: planned. Signals will be separated from execution, UI, and provider code.
7. Event-driven backtester: planned. The backtester will model calendar, liquidity, latency, fees, and T+ settlement constraints.
8. Walk-forward validation: planned. Validation will separate training, selection, and evaluation windows.
9. Paper-trading and execution layer: planned. Any execution integration must be isolated and disabled by default.
10. Application service: planned. Stable services will expose data and analysis functions to the UI.
11. Browser-based HTML user interface: planned. The final tool will be usable through a web browser and will render HTML. The UI technology will be selected later. Strategy and data logic must remain independent of the UI, and the UI must call stable application services rather than contain trading logic.

## Phase 0 Boundaries

Phase 0 does not implement a web framework, frontend build tooling, trading strategies, backtests, machine-learning models, live trading, or portfolio construction. The only provider behavior implemented now is a small reproducible capability audit.
