from __future__ import annotations

import pandas as pd
import pytest
from pydantic import SecretStr

from hose_quant.config import AppSettings
from hose_quant.data.models import (
    CapabilityResult,
    CapabilityStatus,
    ErrorCategory,
    PackageInspection,
)
from hose_quant.data.vnstock_adapter import (
    ProviderProcessTerminatedError,
    VnstockCapabilityAuditor,
    VnstockDataProvider,
    categorize_exception,
    inspect_dataframe,
    sanitize_error,
)


def test_error_categorization_timeout() -> None:
    assert categorize_exception(TimeoutError("timed out")) is ErrorCategory.TIMEOUT


def test_error_categorization_authentication() -> None:
    assert categorize_exception(RuntimeError("401 invalid api key")) is ErrorCategory.AUTHENTICATION


def test_error_categorization_network() -> None:
    assert categorize_exception(RuntimeError("Name resolution failed")) is ErrorCategory.NETWORK


def test_kbs_empty_ohlcv_error_is_not_retried_and_maps_to_empty_frame(tmp_path) -> None:
    settings = AppSettings(
        _env_file=None,
        data_dir=tmp_path / "data",
        report_dir=tmp_path / "reports",
        max_retry_attempts=2,
        provider_sleep_seconds=0,
    )
    provider = VnstockDataProvider(settings)

    class EmptyEquity:
        @staticmethod
        def ohlcv(**_kwargs):  # type: ignore[no-untyped-def]
            raise ValueError("Dữ liệu trống cho mã AAN với interval 1D.")

    class Market:
        @staticmethod
        def equity(_symbol: str) -> EmptyEquity:
            return EmptyEquity()

    provider._market = Market()
    provider._reference = object()
    frame = provider.fetch_daily_ohlcv(
        "AAN",
        start=pd.Timestamp("2020-01-01").date(),
        end=pd.Timestamp("2021-12-30").date(),
    )

    assert frame.empty
    assert frame.columns.tolist() == ["time", "open", "high", "low", "close", "volume"]
    assert provider.call_count == 1


def test_non_empty_provider_value_error_is_not_mapped_to_empty_frame(tmp_path) -> None:
    settings = AppSettings(
        _env_file=None,
        data_dir=tmp_path / "data",
        report_dir=tmp_path / "reports",
        max_retry_attempts=1,
        provider_sleep_seconds=0,
    )
    provider = VnstockDataProvider(settings)

    class InvalidEquity:
        @staticmethod
        def ohlcv(**_kwargs):  # type: ignore[no-untyped-def]
            raise ValueError("Không tìm thấy dữ liệu cho mã INVALID.")

    class Market:
        @staticmethod
        def equity(_symbol: str) -> InvalidEquity:
            return InvalidEquity()

    provider._market = Market()
    provider._reference = object()

    with pytest.raises(ValueError, match="Không tìm thấy dữ liệu"):
        provider.fetch_daily_ohlcv(
            "INVALID",
            start=pd.Timestamp("2020-01-01").date(),
            end=pd.Timestamp("2021-12-30").date(),
        )


def test_vci_daily_request_forwards_official_source_and_count(tmp_path) -> None:
    settings = AppSettings(
        _env_file=None,
        data_dir=tmp_path / "data",
        report_dir=tmp_path / "reports",
        max_retry_attempts=1,
        provider_sleep_seconds=0,
    )
    provider = VnstockDataProvider(settings)
    observed: dict[str, object] = {}

    class Equity:
        @staticmethod
        def ohlcv(**kwargs):  # type: ignore[no-untyped-def]
            observed.update(kwargs)
            return pd.DataFrame(
                [
                    {
                        "time": "2026-07-10",
                        "open": 10,
                        "high": 11,
                        "low": 9,
                        "close": 10,
                        "volume": 100,
                    }
                ]
            )

    class Market:
        @staticmethod
        def equity(symbol: str) -> Equity:
            observed["symbol"] = symbol
            return Equity()

    provider._market = Market()
    provider._reference = object()
    frame = provider.fetch_daily_ohlcv_from_source(
        "fpt",
        start=pd.Timestamp("2026-07-01").date(),
        end=pd.Timestamp("2026-07-10").date(),
        source="VCI",
        count=1200,
    )

    assert len(frame) == 1
    assert observed == {
        "symbol": "FPT",
        "start": "2026-07-01",
        "end": "2026-07-10",
        "resolution": "1D",
        "count": 1200,
        "source": "vci",
    }
    assert provider.call_count == 1


def test_existing_daily_fetch_still_delegates_to_kbs_contract(tmp_path) -> None:
    settings = AppSettings(
        _env_file=None,
        data_dir=tmp_path / "data",
        report_dir=tmp_path / "reports",
        max_retry_attempts=1,
        provider_sleep_seconds=0,
    )
    provider = VnstockDataProvider(settings)
    observed: dict[str, object] = {}

    class Equity:
        @staticmethod
        def ohlcv(**kwargs):  # type: ignore[no-untyped-def]
            observed.update(kwargs)
            return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

    class Market:
        @staticmethod
        def equity(symbol: str) -> Equity:
            observed["symbol"] = symbol
            return Equity()

    provider._market = Market()
    provider._reference = object()
    start = pd.Timestamp("2026-07-01").date()
    end = pd.Timestamp("2026-07-10").date()

    provider.fetch_daily_ohlcv("fpt", start=start, end=end)

    assert observed == {
        "symbol": "FPT",
        "start": "2026-07-01",
        "end": "2026-07-10",
        "resolution": "1D",
        "count": 1000,
        "source": "kbs",
    }
    assert provider.daily_unit_provenance() == provider.daily_unit_provenance_for_source("kbs")


def test_vci_empty_error_is_not_retried_and_maps_to_empty_frame(tmp_path) -> None:
    settings = AppSettings(
        _env_file=None,
        data_dir=tmp_path / "data",
        report_dir=tmp_path / "reports",
        max_retry_attempts=2,
        provider_sleep_seconds=0,
    )
    provider = VnstockDataProvider(settings)

    class EmptyEquity:
        @staticmethod
        def ohlcv(**_kwargs):  # type: ignore[no-untyped-def]
            raise ValueError(
                "Không tìm thấy dữ liệu. Vui lòng kiểm tra lại mã chứng khoán "
                "hoặc thời gian truy xuất."
            )

    class Market:
        @staticmethod
        def equity(_symbol: str) -> EmptyEquity:
            return EmptyEquity()

    provider._market = Market()
    provider._reference = object()
    frame = provider.fetch_daily_ohlcv_from_source(
        "GEE",
        start=pd.Timestamp("2020-01-01").date(),
        end=pd.Timestamp("2021-12-30").date(),
        source="vci",
        count=600,
    )

    assert frame.empty
    assert frame.columns.tolist() == ["time", "open", "high", "low", "close", "volume"]
    assert provider.call_count == 1


def test_daily_source_and_qualification_count_are_bounded(tmp_path) -> None:
    provider = VnstockDataProvider(
        AppSettings(
            _env_file=None,
            data_dir=tmp_path / "data",
            report_dir=tmp_path / "reports",
        )
    )
    start = pd.Timestamp("2026-07-01").date()
    end = pd.Timestamp("2026-07-10").date()

    with pytest.raises(ValueError, match="Unsupported daily OHLCV backend"):
        provider.fetch_daily_ohlcv_from_source("FPT", start, end, source="invented", count=30)
    with pytest.raises(ValueError, match="qualification safety limit"):
        provider.fetch_daily_ohlcv_from_source("FPT", start, end, source="vci", count=1201)
    assert provider.call_count == 0


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


def test_provider_specific_time_can_be_preserved_without_misleading_parse() -> None:
    inspection = inspect_dataframe(
        pd.DataFrame([{"symbol": "FPT", "time": 1783067546136}]),
        parse_timestamps=False,
    )
    assert inspection.earliest_timestamp is None
    assert inspection.timezone_information == "provider-specific/unparsed"
    assert any("provider-specific raw" in item for item in inspection.data_quality_findings)


def test_live_audit_uncertainties_do_not_include_no_live_claim(monkeypatch, tmp_path) -> None:
    settings = AppSettings(
        _env_file=None,
        vnstock_api_key=SecretStr("dummy-live-value"),
        data_dir=tmp_path / "data",
        report_dir=tmp_path / "reports",
    )
    auditor = VnstockCapabilityAuditor(settings)
    monkeypatch.setattr(
        auditor,
        "inspect_package",
        lambda: PackageInspection(package_version="4.0.4"),
    )
    monkeypatch.setattr(
        auditor,
        "_run_live_checks",
        lambda package, api_key: [
            CapabilityResult(
                capability_name="daily historical OHLCV",
                status=CapabilityStatus.VERIFIED,
                elapsed_latency_ms=1,
            )
        ],
    )
    report = auditor.audit_capabilities(live=True)
    assert not any(
        "No live API-key-backed requests" in item for item in report.unresolved_uncertainties
    )


def test_sanitized_error_redacts_known_secret() -> None:
    message = sanitize_error(RuntimeError("bad key secret-123"), ["secret-123"])
    assert "secret-123" not in message
    assert "[REDACTED]" in message


def test_provider_system_exit_is_converted_to_typed_error(tmp_path) -> None:
    settings = AppSettings(
        _env_file=None,
        data_dir=tmp_path / "data",
        report_dir=tmp_path / "reports",
        provider_sleep_seconds=0,
    )
    provider = VnstockDataProvider(settings)

    def terminate() -> None:
        raise SystemExit(1)

    with pytest.raises(ProviderProcessTerminatedError, match="aborting the run"):
        provider._call(terminate)


def test_retry_error_sanitization_includes_root_network_cause() -> None:
    class Attempt:
        @staticmethod
        def exception() -> BaseException:
            return ConnectionError("DNS resolution failed")

    error = RuntimeError("opaque retry wrapper")
    error.last_attempt = Attempt()  # type: ignore[attr-defined]

    message = sanitize_error(error)
    assert "ConnectionError: DNS resolution failed" in message
    assert categorize_exception(error) is ErrorCategory.NETWORK
