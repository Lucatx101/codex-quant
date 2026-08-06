from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

from hose_quant import cli
from hose_quant.config import AppSettings
from hose_quant.data.forensics import (
    CLOSE_RANGE_CATEGORY,
    CURRENT_STALE_CATEGORY,
    HISTORICAL_STALE_CATEGORY,
    MIXED_RANGE_CATEGORY,
    NORMALIZATION_MISMATCH_CATEGORY,
    OPEN_PREVIOUS_CLOSE_CATEGORY,
    POSSIBLE_TRUNCATION_CATEGORY,
    classify_ohlc_relationship_evidence,
    classify_stale_edge,
)
from hose_quant.data.models import DailyCampaignState, DailyUnitProvenance
from hose_quant.data.unit_provenance import VNSTOCK_KBS_DAILY_UNIT_PROVENANCE
from hose_quant.data.workflows import DataWorkflow

FIXED_NOW = datetime(2026, 2, 2, 2, 0, tzinfo=UTC)


def test_ohlc_forensics_distinguishes_open_close_and_mixed_signatures() -> None:
    first_date = date(2020, 5, 8)
    open_date = date(2020, 5, 11)
    open_frame = pd.DataFrame(
        [
            {
                "date": first_date,
                "open": 9.5,
                "high": 10.5,
                "low": 9.0,
                "close": 10.0,
                "volume": 100,
            },
            {
                "date": open_date,
                "open": 10.0,
                "high": 9.8,
                "low": 9.0,
                "close": 9.5,
                "volume": 200,
            },
        ]
    )
    open_result = classify_ohlc_relationship_evidence(
        open_frame,
        previous_close_by_date={open_date: 10.0},
    )
    assert open_result["category"] == OPEN_PREVIOUS_CLOSE_CATEGORY
    assert open_result["open_violation_row_count"] == 1
    assert open_result["open_matches_previous_close_count"] == 1

    close_frame = pd.DataFrame(
        [
            {
                "date": date(2020, 1, 3),
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 12.0,
                "volume": 300,
            }
        ]
    )
    close_result = classify_ohlc_relationship_evidence(close_frame)
    assert close_result["category"] == CLOSE_RANGE_CATEGORY
    assert close_result["relation_counts"] == {"high_below_close": 1}

    mixed = pd.concat([open_frame, close_frame], ignore_index=True)
    mixed_result = classify_ohlc_relationship_evidence(
        mixed,
        previous_close_by_date={open_date: 10.0},
    )
    assert mixed_result["category"] == MIXED_RANGE_CATEGORY


def test_stale_forensics_preserves_unknowns_and_detects_concrete_defects() -> None:
    assert (
        classify_stale_edge(
            task_end=date(2023, 12, 30),
            campaign_end=date(2026, 8, 4),
            last_observation_date=date(2023, 12, 20),
            next_observation_date=date(2024, 1, 2),
            response_row_count=300,
            normalized_matches_raw=True,
        )
        == HISTORICAL_STALE_CATEGORY
    )
    assert (
        classify_stale_edge(
            task_end=date(2026, 8, 4),
            campaign_end=date(2026, 8, 4),
            last_observation_date=date(2026, 7, 20),
            next_observation_date=None,
            response_row_count=100,
            normalized_matches_raw=True,
        )
        == CURRENT_STALE_CATEGORY
    )
    assert (
        classify_stale_edge(
            task_end=date(2026, 8, 4),
            campaign_end=date(2026, 8, 4),
            last_observation_date=date(2026, 7, 20),
            next_observation_date=None,
            response_row_count=100,
            normalized_matches_raw=False,
        )
        == NORMALIZATION_MISMATCH_CATEGORY
    )
    assert (
        classify_stale_edge(
            task_end=date(2023, 12, 30),
            campaign_end=date(2026, 8, 4),
            last_observation_date=date(2023, 12, 20),
            next_observation_date=None,
            response_row_count=1000,
            normalized_matches_raw=True,
        )
        == POSSIBLE_TRUNCATION_CATEGORY
    )


class _ForensicProvider:
    def __init__(self) -> None:
        self.call_count = 0

    def daily_unit_provenance(self) -> DailyUnitProvenance:
        return VNSTOCK_KBS_DAILY_UNIT_PROVENANCE

    def fetch_daily_ohlcv(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        self.call_count += 1
        if start == date(2026, 1, 5):
            return pd.DataFrame(
                [
                    {
                        "time": "2026-01-05",
                        "open": 10.0,
                        "high": 11.0,
                        "low": 9.0,
                        "close": 10.0,
                        "volume": 100,
                    },
                    {
                        "time": "2026-01-06",
                        "open": 10.0,
                        "high": 11.0,
                        "low": 9.0,
                        "close": 12.0,
                        "volume": 200,
                    },
                ]
            )
        return pd.DataFrame(
            [
                {
                    "time": "2026-01-20",
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.0,
                    "volume": 100,
                }
            ]
        )


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(  # type: ignore[call-arg]
        _env_file=None,
        data_dir=tmp_path / "data",
        report_dir=tmp_path / "reports",
        provider_sleep_seconds=0,
        max_retry_attempts=1,
        max_live_provider_calls=2,
        campaign_max_tasks_per_run=1,
        daily_coverage_stale_after_days=7,
    )


def _write_universe(workflow: DataWorkflow) -> None:
    frame = pd.DataFrame(
        [
            {
                "provider": "vnstock",
                "exchange": "HOSE",
                "symbol": "FPT",
                "organ_name": "FPT",
                "english_organ_name": "FPT",
                "security_type": "stock",
                "provider_id": 1,
                "snapshot_timestamp_utc": FIXED_NOW,
                "raw_exchange_field": "HOSE",
                "raw_type_field": "stock",
            }
        ]
    )
    workflow.storage.write_parquet(
        frame,
        workflow.storage.normalized_universe_path(FIXED_NOW.date(), "universe-run"),
    )


def test_forensic_command_classifies_every_unresolved_task_offline(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    settings = _settings(tmp_path)
    provider = _ForensicProvider()
    workflow = DataWorkflow(settings, provider=provider)  # type: ignore[arg-type]
    _write_universe(workflow)
    initialized = workflow.init_daily_campaign(
        campaign_id="forensic-test",
        snapshot_date=FIXED_NOW.date(),
        start=date(2026, 1, 5),
        end=date(2026, 1, 30),
        chunk_calendar_days=15,
    )
    assert initialized.manifest.status == "success"

    failed = workflow.run_daily_campaign(campaign_id="forensic-test", max_tasks=1)
    assert failed.manifest.status == "failed"
    stale = workflow.run_daily_campaign(campaign_id="forensic-test", max_tasks=1)
    assert stale.manifest.status == "success"
    assert provider.call_count == 2

    state_path = workflow.storage.daily_campaign_state_path("forensic-test")
    state = DailyCampaignState.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert state.task_counts["failed"] == 1
    assert state.task_counts["stale"] == 1
    state_before = state_path.read_bytes()

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    exit_code = cli.main(
        [
            "data",
            "forensic-audit-daily-campaign",
            "--campaign-id",
            "forensic-test",
        ]
    )
    output = capsys.readouterr()
    assert exit_code == 0
    assert "Status: success" in output.out
    assert state_path.read_bytes() == state_before

    report_path = next(
        (settings.report_dir / "data_quality/campaigns/forensic-test/forensics").glob(
            "*.json"
        )
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["forensic_audit_contract_version"] == "daily-campaign-forensic-audit-v1"
    summary = report["summary"]
    assert summary["classified_task_count"] == 2
    assert summary["tasks_with_complete_local_evidence"] == 2
    assert summary["tasks_eligible_for_immediate_retry"] == 0
    assert summary["tasks_requiring_code_fix"] == 0
    assert summary["category_counts"][CLOSE_RANGE_CATEGORY]["task_count"] == 1
    assert summary["category_counts"][CURRENT_STALE_CATEGORY]["task_count"] == 1

    failed_record = next(item for item in report["tasks"] if item["state_status"] == "failed")
    assert failed_record["supporting_evidence"]["raw_sha256"]
    assert failed_record["supporting_evidence"]["raw_normalized_comparison"][
        "exact_numeric_and_date_match"
    ] is True
    stale_record = next(item for item in report["tasks"] if item["state_status"] == "stale")
    assert stale_record["supporting_evidence"]["missing_tail_calendar_days"] == 10
    assert stale_record["supporting_evidence"]["event_cause"] == "unresolved"
