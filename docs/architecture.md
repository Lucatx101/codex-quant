# Architecture

This project is a production-grade quantitative research and trading system for Vietnamese equities listed on HOSE. Phase 2 implements the trusted feature-input boundary and a resumable universe-scale daily ingestion campaign on top of the Phase 1 data foundation. Strategy, backtesting, execution, portfolio construction, and the browser UI are future work.

## Planned Layers

1. Provider adapters: implemented for vnstock capability audit and Phase 1 data fetches. Provider methods return provider-shaped frames; normalization lives outside the adapter.
2. Immutable raw-data storage: implemented for Phase 1 under `data/raw/vnstock/<dataset>/<run_id>/`. Files are ignored by Git.
3. Normalized provider market data: implemented for Parquet datasets under `data/normalized/vnstock/`. New KBS daily rows retain versioned provider/backend unit provenance; legacy rows remain valid but unverified. These datasets do not imply historical universe membership.
4. Ingestion campaign and assembly: implemented under `hose_quant.data.campaigns` and the shared workflow boundary. Immutable plans split one observed current-snapshot stock universe into symbol/date tasks. Immutable receipts and child manifests support resume and interruption reconciliation. Campaign audits form a compatible virtual source. Fully resolved campaigns can publish deterministic `assembled-daily-v1` datasets atomically under `data/assembled/vnstock/daily/`, with source run/path lineage on every row.
5. Feature-input layer: implemented under `hose_quant.data`. It prepares auditable current-snapshot universe candidates, backward-looking liquidity characterizations, long-form daily panels, data-availability diagnostics, exact-run and campaign daily coverage audits, versioned contracts, market-time policy, and evidence-derived unit permission. Generated outputs live under `data/feature_inputs/vnstock/` and `data/campaigns/vnstock/daily/`.
6. Historical universe and corporate-action layer: unresolved. Current snapshots cannot establish historical membership, and adjusted-price/corporate-action completeness is unknown.
7. Feature engine: planned. Feature code will consume only validated Phase 2 panel contracts.
8. Signal engine: planned. Signals will be separated from execution, UI, and provider code.
9. Event-driven backtester: planned. The backtester will model calendar, liquidity, latency, fees, and T+ settlement constraints.
10. Walk-forward validation: planned. Validation will separate training, selection, and evaluation windows.
11. Paper-trading and execution layer: planned. Any execution integration must be isolated and disabled by default.
12. Application service: planned. Stable services will expose data and analysis functions to the UI.
13. Browser-based HTML user interface: planned. The final tool will be usable through a web browser and will render HTML. The UI technology will be selected later. Strategy and data logic must remain independent of the UI, and the UI must call stable application services rather than contain trading logic.

## Phase Boundaries

Phase 2 does not implement alpha features, labels, signals, a web framework, frontend build tooling, trading strategies, backtests, machine-learning models, live trading, or portfolio construction.

Campaign assembly is a structurally compatible candidate input, not proof of historical universe
membership, adjusted prices, corporate-action completeness, or complete exchange-calendar
coverage. See [universe-ingestion-campaign.md](universe-ingestion-campaign.md).
