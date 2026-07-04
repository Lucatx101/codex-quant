# Project Agent Notes

- Inspect repository and Git state before editing.
- Read relevant provider documentation before changing vnstock code.
- Use official vnstock methods only; do not invent endpoints, parameters, or capabilities.
- Preserve point-in-time correctness and avoid look-ahead and survivorship assumptions.
- Protect secrets. Do not commit `.env`, API keys, tokens, auth headers, logs, or generated market data.
- Keep trading logic independent of the future browser UI.
- Keep tests offline by default; live audits must be explicit.
- Update tests when behavior changes.
- Update documentation when architecture, commands, or verified capabilities change.
- Do not implement strategy, backtesting, ML, live trading, portfolio construction, or web UI work unless the active phase explicitly requests it.
- Keep generated raw data, normalized Parquet, manifests, and data-quality reports out of Git unless a later prompt explicitly asks for a sanitized sample.
- Run `make check` before committing.
- Do not commit failed experiments.
- Stop and report when a required provider capability cannot be verified.
