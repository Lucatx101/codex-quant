from __future__ import annotations

from hose_quant.data.models import CapabilityResult, CapabilityStatus


def test_capability_status_serializes_as_value() -> None:
    result = CapabilityResult(
        capability_name="daily historical OHLCV",
        status=CapabilityStatus.DOCUMENTED_NOT_TESTED,
    )
    payload = result.model_dump(mode="json")
    assert payload["status"] == "DOCUMENTED_NOT_TESTED"
    assert payload["capability_name"] == "daily historical OHLCV"
