from __future__ import annotations

import json
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest

from hose_quant import cli
from hose_quant.config import PROJECT_ROOT, AppSettings
from hose_quant.data.coverage import audit_daily_coverage, summarize_daily_coverage
from hose_quant.data.manifests import build_manifest, write_manifest
from hose_quant.data.models import DailyCoverageConfig, DailyCoverageStatus
from hose_quant.data.normalizers import normalize_daily_ohlcv
from hose_quant.data.storage import DataStorage
from hose_quant.data.unit_provenance import (
    SOURCE_SPECIFIC_PROVENANCE_COLUMNS,
    VNSTOCK_KBS_DAILY_UNIT_PROVENANCE,
)
from hose_quant.data.validators import has_blocking_errors, validate_daily_coverage
from hose_quant.data.vnstock_adapter import ProviderProcessTerminatedError
from hose_quant.data.workflows import DataWorkflow, daily_date_chunks

FIXED_NOW = datetime(2026, 8, 5, 2, 0, tzinfo=UTC)


def _raw_daily(start: date, end: date) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "time": value.date().isoformat(),
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10,
                "volume": 100,
            }
            for value in pd.bdate_range(start, end)
        ]
    )


def _verified_daily(symbol: str, start: date, end: date) -> pd.DataFrame:
    return normalize_daily_ohlcv(
        _raw_daily(start, end),
        symbol=symbol,
        exchange="HOSE",
        ingestion_timestamp_utc=FIXED_NOW,
        unit_provenance=VNSTOCK_KBS_DAILY_UNIT_PROVENANCE,
    )


def _universe_snapshot() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "provider": "vnstock",
                "exchange": "HOSE",
                "symbol": symbol,
                "organ_name": symbol,
                "english_organ_name": symbol,
                "security_type": "stock",
                "provider_id": index,
                "snapshot_timestamp_utc": FIXED_NOW,
                "raw_exchange_field": "HOSE",
                "raw_type_field": "stock",
            }
            for index, symbol in enumerate(["FPT", "HPG"], start=1)
        ]
    )


class FakeDailyProvider:
    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.call_count = 0
        self.fail_on_call = fail_on_call
        self.requests: list[tuple[str, date, date]] = []

    def daily_unit_provenance(self):  # type: ignore[no-untyped-def]
        return VNSTOCK_KBS_DAILY_UNIT_PROVENANCE

    def fetch_daily_ohlcv(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        self.call_count += 1
        self.requests.append((symbol, start, end))
        if self.call_count == self.fail_on_call:
            raise RuntimeError("provider unavailable")
        return _raw_daily(start, end)


class BoundaryDailyProvider(FakeDailyProvider):
    def fetch_daily_ohlcv(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        self.call_count += 1
        self.requests.append((symbol, start, end))
        row = {
            "time": start.isoformat(),
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 10,
            "volume": 100,
        }
        return pd.DataFrame([row] * 1000)


class TerminatingDailyProvider(FakeDailyProvider):
    def fetch_daily_ohlcv(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        self.call_count += 1
        self.requests.append((symbol, start, end))
        if self.call_count == 2:
            raise ProviderProcessTerminatedError("provider terminated due to rate-limit")
        return _raw_daily(start, end)


def test_daily_chunks_are_bounded_contiguous_and_non_overlapping() -> None:
    chunks = daily_date_chunks(
        date(2020, 1, 1),
        date(2026, 8, 4),
        chunk_calendar_days=730,
    )

    assert len(chunks) == 4
    assert chunks[0][0] == date(2020, 1, 1)
    assert chunks[-1][1] == date(2026, 8, 4)
    assert all(
        next_start == previous_end + pd.Timedelta(days=1)
        for (_, previous_end), (next_start, _) in zip(chunks[:-1], chunks[1:], strict=True)
    )
    with pytest.raises(ValueError, match="1,095-day"):
        daily_date_chunks(
            date(2020, 1, 1),
            date(2026, 8, 4),
            chunk_calendar_days=1096,
        )


def test_backfill_daily_chunks_requests_and_writes_one_complete_run(tmp_path: Path) -> None:
    settings = AppSettings(
        _env_file=None,
        data_dir=tmp_path / "data",
        report_dir=tmp_path / "reports",
        provider_sleep_seconds=0,
    )
    provider = FakeDailyProvider()
    workflow = DataWorkflow(settings, provider=provider)  # type: ignore[arg-type]

    result = workflow.backfill_daily(
        symbols=["FPT"],
        start=date(2026, 7, 1),
        end=date(2026, 7, 10),
        chunk_calendar_days=4,
    )

    assert result.manifest.status == "success"
    assert result.manifest.provider_call_count == 3
    assert result.manifest.row_counts["chunks_succeeded"] == 3
    assert provider.requests == [
        ("FPT", date(2026, 7, 1), date(2026, 7, 4)),
        ("FPT", date(2026, 7, 5), date(2026, 7, 8)),
        ("FPT", date(2026, 7, 9), date(2026, 7, 10)),
    ]
    normalized = workflow.storage.read_normalized_dataset(
        "daily",
        run_id=result.manifest.run_id,
    )
    assert normalized is not None
    assert len(normalized) == 8
    assert not normalized.duplicated(["symbol", "date"]).any()
    assert set(normalized["source_unit_policy_name"]) == {"vnstock-kbs-daily-ohlcv"}


def test_failed_chunk_does_not_publish_partial_normalized_run(tmp_path: Path) -> None:
    settings = AppSettings(
        _env_file=None,
        data_dir=tmp_path / "data",
        report_dir=tmp_path / "reports",
        provider_sleep_seconds=0,
    )
    workflow = DataWorkflow(
        settings,
        provider=FakeDailyProvider(fail_on_call=2),  # type: ignore[arg-type]
    )

    result = workflow.backfill_daily(
        symbols=["FPT"],
        start=date(2026, 7, 1),
        end=date(2026, 7, 10),
        chunk_calendar_days=4,
    )

    assert result.manifest.status == "failed"
    assert result.manifest.provider_call_count == 2
    assert result.manifest.row_counts["chunks_succeeded"] == 1
    assert workflow.storage.normalized_dataset_paths(
        "daily",
        run_id=result.manifest.run_id,
    ) == []
    assert any("provider unavailable" in error for error in result.manifest.error_summary)


def test_thousand_bar_boundary_is_retained_raw_but_not_published(tmp_path: Path) -> None:
    settings = AppSettings(
        _env_file=None,
        data_dir=tmp_path / "data",
        report_dir=tmp_path / "reports",
        provider_sleep_seconds=0,
    )
    workflow = DataWorkflow(
        settings,
        provider=BoundaryDailyProvider(),  # type: ignore[arg-type]
    )

    result = workflow.backfill_daily(
        symbols=["FPT"],
        start=date(2026, 7, 1),
        end=date(2026, 7, 10),
        chunk_calendar_days=30,
    )

    assert result.manifest.status == "failed"
    assert result.manifest.row_counts["raw"] == 1000
    assert result.manifest.row_counts["normalized"] == 0
    assert any("1,000-bar safety boundary" in error for error in result.manifest.error_summary)
    assert workflow.storage.normalized_dataset_paths(
        "daily",
        run_id=result.manifest.run_id,
    ) == []
    assert any(path.endswith("raw.jsonl") for path in result.manifest.output_paths)


def test_provider_termination_stops_remaining_chunks_and_writes_failure(tmp_path: Path) -> None:
    settings = AppSettings(
        _env_file=None,
        data_dir=tmp_path / "data",
        report_dir=tmp_path / "reports",
        provider_sleep_seconds=0,
    )
    provider = TerminatingDailyProvider()
    workflow = DataWorkflow(settings, provider=provider)  # type: ignore[arg-type]

    result = workflow.backfill_daily(
        symbols=["FPT"],
        start=date(2026, 7, 1),
        end=date(2026, 7, 10),
        chunk_calendar_days=4,
    )

    assert result.manifest.status == "failed"
    assert provider.call_count == 2
    assert result.manifest.row_counts["chunks_projected"] == 3
    assert result.manifest.row_counts["chunks_succeeded"] == 1
    assert result.manifest.row_counts["raw"] > 0
    assert workflow.storage.normalized_dataset_paths(
        "daily",
        run_id=result.manifest.run_id,
    ) == []


def test_backfill_dry_run_prints_projected_provider_calls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = AppSettings(
        _env_file=None,
        data_dir=tmp_path / "data",
        report_dir=tmp_path / "reports",
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    exit_code = cli.main(
        [
            "data",
            "backfill-daily",
            "--symbols",
            "FPT,HPG",
            "--start",
            "2020-01-01",
            "--end",
            "2026-08-04",
            "--chunk-calendar-days",
            "730",
            "--dry-run",
        ]
    )

    assert exit_code == 0
    assert "Plan projected_provider_call_count: 8" in capsys.readouterr().out


def test_daily_coverage_classifies_usable_sparse_and_absent_symbols() -> None:
    daily = pd.concat(
        [
            _verified_daily("FPT", date(2026, 7, 27), date(2026, 7, 31)),
            _verified_daily("VCB", date(2026, 7, 30), date(2026, 7, 31)),
        ],
        ignore_index=True,
    )
    daily["__input_path"] = "/tmp/source.parquet"
    coverage = audit_daily_coverage(
        daily,
        current_universe_symbols={"FPT", "HPG", "VCB"},
        requested_symbols={"FPT", "HPG", "VCB"},
        universe_snapshot_date=date(2026, 8, 5),
        daily_run_id="source-run",
        start=date(2026, 7, 27),
        end=date(2026, 7, 31),
        config=DailyCoverageConfig(
            min_history_observations=5,
            min_span_coverage_ratio=0.8,
            stale_after_calendar_days=7,
            max_zero_volume_frequency=0.2,
        ),
    )

    status_by_symbol = coverage.set_index("symbol")["coverage_status"].to_dict()
    assert status_by_symbol == {
        "HPG": DailyCoverageStatus.ABSENT.value,
        "VCB": DailyCoverageStatus.INSUFFICIENT_HISTORY.value,
        "FPT": DailyCoverageStatus.USABLE_VND.value,
    }
    fpt = coverage[coverage["symbol"] == "FPT"].iloc[0]
    assert bool(fpt["vnd_liquidity_research_usable"])
    assert not bool(fpt["adjusted_price_research_usable"])
    assert not bool(fpt["point_in_time_universe_research_usable"])
    assert not has_blocking_errors(
        validate_daily_coverage(coverage, expected_symbol_count=3)
    )
    summary = summarize_daily_coverage(coverage)
    assert summary["vnd_liquidity_usable_symbols"] == ["FPT"]


def test_coverage_cli_uses_exact_successful_run_and_makes_no_provider_calls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = AppSettings(
        _env_file=None,
        data_dir=tmp_path / "data",
        report_dir=tmp_path / "reports",
    )
    storage = DataStorage(settings.data_dir)
    storage.ensure_layout()
    source_run_id = "verified-source-run"
    storage.write_parquet(
        _verified_daily("FPT", date(2026, 7, 27), date(2026, 7, 31)),
        storage.normalized_daily_path("FPT", source_run_id),
    )
    legacy = _verified_daily("FPT", date(2026, 7, 27), date(2026, 7, 31)).drop(
        columns=list(SOURCE_SPECIFIC_PROVENANCE_COLUMNS)
    )
    storage.write_parquet(legacy, storage.normalized_daily_path("FPT", "legacy-other-run"))
    storage.write_parquet(
        _universe_snapshot(),
        storage.normalized_universe_path(FIXED_NOW.date(), "universe-source-run"),
    )
    source_manifest = build_manifest(
        run_id=source_run_id,
        command="data backfill-daily",
        started_at_utc=FIXED_NOW,
        finished_at_utc=FIXED_NOW,
        status="success",
        symbols=["FPT"],
        provider_call_count=1,
    )
    write_manifest(source_manifest, storage.manifest_root)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    exit_code = cli.main(
        [
            "data",
            "audit-daily-coverage",
            "--daily-run-id",
            source_run_id,
            "--start",
            "2026-07-27",
            "--end",
            "2026-07-31",
            "--snapshot-date",
            "2026-08-05",
            "--min-history-observations",
            "5",
            "--min-span-coverage-ratio",
            "0.8",
        ]
    )

    assert exit_code == 0
    assert "Status: success" in capsys.readouterr().out
    audit_manifests = [
        json.loads(path.read_text())
        for path in storage.manifest_root.glob("*.json")
        if json.loads(path.read_text())["command"] == "data audit-daily-coverage"
    ]
    assert len(audit_manifests) == 1
    audit_manifest = audit_manifests[0]
    assert audit_manifest["provider_call_count"] == 0
    assert audit_manifest["unit_provenance"]["provenance_status"] == "verified"
    assert audit_manifest["row_counts"]["vnd_liquidity_usable_symbols"] == 1
    report_path = next(
        Path(path)
        for path in audit_manifest["output_paths"]
        if path.endswith("-daily-coverage.json")
    )
    report = json.loads(report_path.read_text())
    assert report["summary"]["vnd_liquidity_usable_symbols"] == ["FPT"]
    assert report["summary"]["status_counts"] == {
        "not_ingested": 1,
        "usable_vnd": 1,
    }


def test_phase_22_generated_outputs_are_ignored_by_git() -> None:
    paths = [
        (
            "data/feature_inputs/vnstock/coverage/snapshot_date=2026-08-05/"
            "start_date=2020-01-01/end_date=2026-08-04/audit.parquet"
        ),
        "reports/data_quality/audit-daily-coverage.json",
    ]
    result = subprocess.run(
        ["git", "check-ignore", *paths],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert set(paths) <= set(result.stdout.splitlines())
