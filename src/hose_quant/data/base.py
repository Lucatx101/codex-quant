from __future__ import annotations

from datetime import date
from typing import Protocol

import pandas as pd

from hose_quant.data.models import AuditReport


class MarketDataProvider(Protocol):
    """Minimal interface for provider capability discovery."""

    def audit_capabilities(self, *, live: bool) -> AuditReport:
        """Inspect provider capabilities and optionally perform live checks."""


class VnstockFetchProvider(Protocol):
    """Provider-shaped fetch interface used by data workflows."""

    call_count: int

    def fetch_universe(self, exchange: str) -> pd.DataFrame:
        """Fetch current exchange universe data."""

    def fetch_daily_ohlcv(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Fetch daily OHLCV for one symbol."""

    def fetch_intraday_bars(
        self,
        symbol: str,
        resolution: str,
        lookback_days: int,
    ) -> pd.DataFrame:
        """Fetch intraday bars for one symbol."""

    def fetch_quote_snapshot(self, symbols: list[str]) -> pd.DataFrame:
        """Fetch a batch quote snapshot."""
