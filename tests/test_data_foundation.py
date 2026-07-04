from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime

import pandas as pd

from hose_quant.config import PROJECT_ROOT
from hose_quant.data.manifests import build_manifest, write_manifest
from hose_quant.data.models import ProviderTimeParseStatus, ValidationSeverity
from hose_quant.data.normalizers import (
    normalize_daily_ohlcv,
    normalize_intraday_bars,
    normalize_quote_snapshot,
    normalize_universe_snapshot,
)
from hose_quant.data.storage import DataStorage
from hose_quant.data.validators import (
    has_blocking_errors,
    validate_daily_ohlcv,
    validate_intraday_bars,
    validate_quote_snapshot,
    validate_universe_snapshot,
    write_validation_reports,
)

FIXED_NOW = datetime(2026, 7, 4, 0, 0, tzinfo=UTC)


def test_universe_normalization_filters_hose_and_reports_diagnostics() -> None:
    raw = pd.DataFrame(
        [
            {
                "symbol": "FPT",
                "organ_name": "FPT",
                "en_organ_name": "FPT",
                "exchange": "HOSE",
                "type": "stock",
                "id": 1,
            },
            {
                "symbol": "AAA",
                "organ_name": None,
                "en_organ_name": None,
                "exchange": None,
                "type": None,
                "id": 2,
            },
            {
                "symbol": "HNX1",
                "organ_name": "HNX",
                "en_organ_name": "HNX",
                "exchange": "HNX",
                "type": "bond",
                "id": 3,
            },
        ]
    )
    normalized, diagnostics = normalize_universe_snapshot(
        raw, exchange="HOSE", snapshot_timestamp_utc=FIXED_NOW
    )
    assert normalized["symbol"].tolist() == ["FPT"]
    assert diagnostics.total_returned_rows == 3
    assert diagnostics.hose_rows == 1
    assert diagnostics.null_exchange_rows == 1
    results = validate_universe_snapshot(normalized, diagnostics)
    assert any(result.check_name == "hose_filtering" for result in results)
    assert any(result.check_name == "null_exchange_fields" for result in results)


def test_daily_normalization_sorts_and_validator_detects_duplicates_and_bad_ohlc() -> None:
    raw = pd.DataFrame(
        [
            {"time": "2026-01-02", "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100},
            {"time": "2026-01-01", "open": 10, "high": 8, "low": 9, "close": 11, "volume": 100.5},
            {"time": "2026-01-01", "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100},
        ]
    )
    normalized = normalize_daily_ohlcv(raw, symbol="fpt", ingestion_timestamp_utc=FIXED_NOW)
    assert normalized["date"].astype(str).tolist() == ["2026-01-01", "2026-01-01", "2026-01-02"]
    results = validate_daily_ohlcv(normalized)
    checks = {result.check_name for result in results}
    assert "duplicate_symbol_date" in checks
    assert "high_gte_low" in checks
    assert "volume_integer_like" in checks
    assert has_blocking_errors(results)


def test_intraday_validation_detects_duplicate_and_timezone_naive_timestamp() -> None:
    raw = pd.DataFrame(
        [
            {
                "time": "2026-07-03 09:15:00",
                "open": 1,
                "high": 2,
                "low": 1,
                "close": 2,
                "volume": 10,
            },
            {
                "time": "2026-07-03 09:15:00",
                "open": 1,
                "high": 2,
                "low": 1,
                "close": 2,
                "volume": 10,
            },
        ]
    )
    normalized = normalize_intraday_bars(
        raw, symbol="FPT", resolution="1m", ingestion_timestamp_utc=FIXED_NOW
    )
    results = validate_intraday_bars(normalized)
    checks = {result.check_name for result in results}
    assert "duplicate_symbol_resolution_timestamp" in checks
    assert "timestamp_timezone_naive" in checks


def test_quote_normalization_preserves_numeric_provider_time_without_1970_parse() -> None:
    raw = pd.DataFrame(
        [
            {
                "symbol": "FPT",
                "time": 1783067546136,
                "exchange": "HOSE",
                "reference_price": 100,
                "volume_accumulated": 1000,
            }
        ]
    )
    normalized, missing = normalize_quote_snapshot(
        raw, requested_symbols=["FPT", "HPG"], snapshot_timestamp_utc=FIXED_NOW
    )
    assert missing == ["HPG"]
    assert normalized.loc[0, "provider_time_raw"] == "1783067546136"
    assert pd.isna(normalized.loc[0, "provider_time_parsed"])
    assert (
        normalized.loc[0, "provider_time_parse_status"]
        == ProviderTimeParseStatus.PROVIDER_SPECIFIC_UNPARSED.value
    )
    results = validate_quote_snapshot(
        normalized, requested_symbols=["FPT", "HPG"], missing_symbols=missing
    )
    assert any(result.check_name == "missing_requested_symbols" for result in results)
    assert not has_blocking_errors(results)


def test_quote_validator_blocks_misleading_1970_timestamp() -> None:
    frame = pd.DataFrame(
        [
            {
                "provider": "vnstock",
                "symbol": "FPT",
                "snapshot_timestamp_utc": FIXED_NOW,
                "provider_time_raw": "1783067546136",
                "provider_time_parsed": pd.Timestamp("1970-01-01"),
                "provider_time_parse_status": "parsed",
                "exchange": "HOSE",
            }
        ]
    )
    results = validate_quote_snapshot(frame, requested_symbols=["FPT"])
    assert any(result.check_name == "provider_time_not_misleading_1970" for result in results)
    assert has_blocking_errors(results)


def test_storage_paths_and_manifest_creation(tmp_path) -> None:
    storage = DataStorage(tmp_path / "data")
    storage.ensure_layout()
    daily_path = storage.normalized_daily_path("fpt", "run-1")
    intraday_path = storage.normalized_intraday_path(
        resolution="1m",
        symbol="FPT",
        trading_date=FIXED_NOW.date(),
        run_id="run-1",
    )
    assert "symbol=FPT" in str(daily_path)
    assert "resolution=1m" in str(intraday_path)
    manifest = build_manifest(
        run_id="run-1",
        command="data validate",
        started_at_utc=FIXED_NOW,
        finished_at_utc=FIXED_NOW,
        status="success",
    )
    path = write_manifest(manifest, storage.manifest_root)
    assert json.loads(path.read_text())["run_id"] == "run-1"


def test_data_quality_reports_are_generated_from_validation_results(tmp_path) -> None:
    result = [
        {
            "dataset_name": "daily",
            "severity": ValidationSeverity.INFO,
            "check_name": "sample",
            "message": "ok",
        }
    ]
    from hose_quant.data.models import ValidationResult

    validation_results = [ValidationResult(**item) for item in result]
    json_path, markdown_path = write_validation_reports(
        validation_results,
        json_path=tmp_path / "latest.json",
        markdown_path=tmp_path / "latest.md",
    )
    assert json.loads(json_path.read_text())["summary"]["INFO"] == 1
    assert "# Data Quality Report" in markdown_path.read_text()


def test_generated_market_data_paths_are_ignored_by_git() -> None:
    paths = [
        "data/raw/vnstock/daily/run/raw.jsonl",
        "data/normalized/vnstock/daily/symbol=FPT/run.parquet",
        "data/manifests/run.json",
        "reports/data_quality/latest.json",
    ]
    result = subprocess.run(
        ["git", "check-ignore", *paths],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    ignored = set(result.stdout.splitlines())
    assert set(paths) <= ignored
