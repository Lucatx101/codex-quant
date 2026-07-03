from __future__ import annotations

from typing import Protocol

from hose_quant.data.models import AuditReport


class MarketDataProvider(Protocol):
    """Minimal interface for provider capability discovery."""

    def audit_capabilities(self, *, live: bool) -> AuditReport:
        """Inspect provider capabilities and optionally perform live checks."""
