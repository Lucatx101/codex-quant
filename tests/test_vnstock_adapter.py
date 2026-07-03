from __future__ import annotations

import pandas as pd

from hose_quant.data.models import ErrorCategory
from hose_quant.data.vnstock_adapter import categorize_exception, inspect_dataframe, sanitize_error


def test_error_categorization_timeout() -> None:
    assert categorize_exception(TimeoutError("timed out")) is ErrorCategory.TIMEOUT


def test_error_categorization_authentication() -> None:
    assert categorize_exception(RuntimeError("401 invalid api key")) is ErrorCategory.AUTHENTICATION


def test_error_categorization_network() -> None:
    assert categorize_exception(RuntimeError("Name resolution failed")) is ErrorCategory.NETWORK


def test_empty_response_mapping() -> None:
    inspection = inspect_dataframe(pd.DataFrame(columns=["time", "open"]))
    assert inspection.error_category is ErrorCategory.EMPTY_RESPONSE
    assert inspection.row_count == 0


def test_malformed_schema_mapping() -> None:
    inspection = inspect_dataframe(
        pd.DataFrame([{"time": "2024-01-01"}]), required_columns={"time", "close"}
    )
    assert inspection.error_category is ErrorCategory.INVALID_SCHEMA
    assert any("Missing required columns" in item for item in inspection.data_quality_findings)


def test_duplicate_and_unsorted_timestamps_are_reported() -> None:
    frame = pd.DataFrame(
        [
            {"time": "2024-01-03", "close": 3},
            {"time": "2024-01-02", "close": 2},
            {"time": "2024-01-02", "close": 2},
        ],
    )
    inspection = inspect_dataframe(frame, required_columns={"time", "close"})
    findings = " ".join(inspection.data_quality_findings)
    assert "Duplicate timestamps" in findings
    assert "not sorted ascending" in findings


def test_sanitized_error_redacts_known_secret() -> None:
    message = sanitize_error(RuntimeError("bad key secret-123"), ["secret-123"])
    assert "secret-123" not in message
    assert "[REDACTED]" in message
