# Architecture

This project is a production-grade quantitative research and trading system for Vietnamese equities listed on HOSE. Phase 1 implements the data foundation only. Strategy, backtesting, execution, portfolio construction, and the browser UI are future work.

## Planned Layers

1. Provider adapters: implemented for vnstock capability audit and Phase 1 data fetches. Provider methods return provider-shaped frames; normalization lives outside the adapter.
2. Immutable raw-data storage: implemented for Phase 1 under `data/raw/vnstock/<dataset>/<run_id>/`. Files are ignored by Git.
3. Normalized point-in-time market data: implemented for Phase 1 Parquet datasets under `data/normalized/vnstock/`.
4. Universe and corporate-action handling: partially implemented. Current universe snapshots are normalized; point-in-time historical universe and corporate-action completeness remain unresolved.
5. Feature engine: planned. Feature code will consume normalized point-in-time data only.
6. Signal engine: planned. Signals will be separated from execution, UI, and provider code.
7. Event-driven backtester: planned. The backtester will model calendar, liquidity, latency, fees, and T+ settlement constraints.
8. Walk-forward validation: planned. Validation will separate training, selection, and evaluation windows.
9. Paper-trading and execution layer: planned. Any execution integration must be isolated and disabled by default.
10. Application service: planned. Stable services will expose data and analysis functions to the UI.
11. Browser-based HTML user interface: planned. The final tool will be usable through a web browser and will render HTML. The UI technology will be selected later. Strategy and data logic must remain independent of the UI, and the UI must call stable application services rather than contain trading logic.

## Phase Boundaries

Phase 1 does not implement a web framework, frontend build tooling, trading strategies, backtests, machine-learning models, live trading, or portfolio construction.
