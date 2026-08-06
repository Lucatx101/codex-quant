from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd

from hose_quant.config import AppSettings
from hose_quant.data.contracts import (
    DAILY_CAMPAIGN_CONTRACT_VERSION,
    NORMALIZED_DAILY_CONTRACT_VERSION,
)
from hose_quant.data.models import (
    DailyCampaignPlan,
    DailyCampaignState,
    DailyCampaignTask,
)
from hose_quant.data.normalizers import normalize_daily_ohlcv
from hose_quant.data.source_qualification import (
    VCI_QUALIFICATION_PROBES,
    VciQualificationProbe,
    _adjustment_assessment,
    derive_vci_verdict,
    execute_vci_source_qualification,
    inspect_probe_response,
)
from hose_quant.data.storage import DataStorage
from hose_quant.data.unit_provenance import (
    VNSTOCK_KBS_DAILY_UNIT_PROVENANCE,
    VNSTOCK_VCI_DAILY_UNIT_PROVENANCE,
    resolve_daily_unit_policy,
)
from hose_quant.data.workflows import DataWorkflow

FIXED_NOW = datetime(2026, 8, 6, 3, 0, tzinfo=UTC)


class FakeVciProvider:
    def __init__(self) -> None:
        self.call_count = 0

    def fetch_daily_ohlcv_from_source(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        source: str,
        count: int,
    ) -> pd.DataFrame:
        del start
        assert source == "vci"
        self.call_count += 1
        if symbol == "GEE":
            return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
        dates = pd.bdate_range(end=end, periods=count)
        return pd.DataFrame(
            {
                "time": dates,
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "volume": 100,
            }
        )

    @staticmethod
    def daily_unit_provenance_for_source(_source: str):  # type: ignore[no-untyped-def]
        return VNSTOCK_VCI_DAILY_UNIT_PROVENANCE


class PreStartInvalidFakeVciProvider(FakeVciProvider):
    def fetch_daily_ohlcv_from_source(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        source: str,
        count: int,
    ) -> pd.DataFrame:
        frame = super().fetch_daily_ohlcv_from_source(
            symbol,
            start,
            end,
            source=source,
            count=count,
        )
        if not frame.empty:
            before_start = pd.to_datetime(frame["time"]).dt.date < start
            if before_start.any():
                frame.loc[before_start.idxmax(), "high"] = 5.0
            before_start_indexes = frame.index[before_start]
            if count == 30 and len(before_start_indexes) >= 2:
                frame.loc[before_start_indexes[1], "time"] = frame.loc[
                    before_start_indexes[0], "time"
                ]
        return frame


class EmptyWindowFakeVciProvider(FakeVciProvider):
    def fetch_daily_ohlcv_from_source(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        source: str,
        count: int,
    ) -> pd.DataFrame:
        if symbol != "ACL":
            return super().fetch_daily_ohlcv_from_source(
                symbol,
                start,
                end,
                source=source,
                count=count,
            )
        assert source == "vci"
        self.call_count += 1
        return pd.DataFrame(
            [
                {
                    "time": "2019-01-02",
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.5,
                    "volume": 100,
                }
            ]
        )


def _campaign_files(storage: DataStorage) -> tuple[DailyCampaignPlan, DailyCampaignState]:
    task = DailyCampaignTask(
        task_id="FPT__2020-01-01__2026-08-04",
        symbol="FPT",
        start_date=date(2020, 1, 1),
        end_date=date(2026, 8, 4),
    )
    plan = DailyCampaignPlan(
        campaign_contract_version=DAILY_CAMPAIGN_CONTRACT_VERSION,
        campaign_id="test-vci-qualification",
        provider="vnstock",
        data_backend="kbs",
        exchange="HOSE",
        source_resolution="1D",
        normalized_daily_contract_version=NORMALIZED_DAILY_CONTRACT_VERSION,
        price_adjustment_semantics="unknown_provider_adjustment_flag",
        universe_snapshot_date=date(2026, 8, 5),
        universe_snapshot_observed_at_utc=FIXED_NOW,
        universe_run_ids=["universe-run"],
        universe_input_paths=["universe.parquet"],
        start_date=date(2020, 1, 1),
        end_date=date(2026, 8, 4),
        chunk_calendar_days=2500,
        stale_after_calendar_days=7,
        symbols=["FPT"],
        expected_unit_provenance=VNSTOCK_KBS_DAILY_UNIT_PROVENANCE,
        tasks=[task],
        created_at_utc=FIXED_NOW,
    )
    state = DailyCampaignState(
        campaign_contract_version=DAILY_CAMPAIGN_CONTRACT_VERSION,
        campaign_id=plan.campaign_id,
        task_counts={"pending": 1},
        symbol_counts={"pending": 1},
    )
    plan_path = storage.daily_campaign_plan_path(plan.campaign_id)
    state_path = storage.daily_campaign_state_path(plan.campaign_id)
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(plan.model_dump_json(indent=2) + "\n", encoding="utf-8")
    state_path.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return plan, state


def test_vci_and_kbs_unit_contracts_are_registered_but_cannot_mix() -> None:
    raw = pd.DataFrame(
        [
            {
                "time": "2026-07-01",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10,
                "volume": 100,
            }
        ]
    )
    vci = normalize_daily_ohlcv(
        raw,
        symbol="FPT",
        unit_provenance=VNSTOCK_VCI_DAILY_UNIT_PROVENANCE,
    )
    kbs = normalize_daily_ohlcv(
        raw,
        symbol="HPG",
        unit_provenance=VNSTOCK_KBS_DAILY_UNIT_PROVENANCE,
    )

    assert resolve_daily_unit_policy(vci).vnd_traded_value_permitted
    mixed = resolve_daily_unit_policy(pd.concat([vci, kbs], ignore_index=True))
    assert not mixed.vnd_traded_value_permitted
    assert mixed.provenance_status.value == "ambiguous"


def test_probe_inspection_preserves_start_semantics_and_ohlc_checks() -> None:
    probe = VciQualificationProbe(
        probe_id="boundary",
        case_class="test",
        symbol="FPT",
        start=date(2026, 7, 1),
        end=date(2026, 7, 3),
        count=4,
        required_boundary_dates=(date(2026, 7, 1), date(2026, 7, 3)),
    )
    raw = pd.DataFrame(
        [
            {"time": "2026-06-30", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1},
            {"time": "2026-07-01", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1},
            {"time": "2026-07-02", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1},
            {"time": "2026-07-03", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1},
        ]
    )
    normalized = normalize_daily_ohlcv(
        raw,
        symbol="FPT",
        unit_provenance=VNSTOCK_VCI_DAILY_UNIT_PROVENANCE,
    )

    result = inspect_probe_response(raw, normalized, probe=probe)

    assert result["rows_before_requested_start"] == 1
    assert result["request_window_row_count"] == 3
    assert result["boundary_dates_missing"] == []
    assert result["blocking_ohlc_check_count"] == 0
    reversed_raw = raw.iloc[::-1].reset_index(drop=True)
    reversed_normalized = normalize_daily_ohlcv(
        reversed_raw,
        symbol="FPT",
        unit_provenance=VNSTOCK_VCI_DAILY_UNIT_PROVENANCE,
    )
    reversed_result = inspect_probe_response(
        reversed_raw,
        reversed_normalized,
        probe=probe,
    )
    assert reversed_result["raw_time_ordering"] == "not_ascending"
    assert reversed_result["response_value_digest_sha256"] != result["response_value_digest_sha256"]


def test_verdict_is_derived_mechanically_and_unknown_wins_on_incomplete_run() -> None:
    criteria = {
        "all_bounded_probes_executed": True,
        "schema_and_date_labels_valid": True,
        "requested_window_observations_match_probe_expectation": True,
        "ohlc_invariants_valid": True,
        "duplicate_dates_absent": True,
        "empty_response_behavior_verified": True,
        "end_inclusivity_verified": True,
        "start_semantics_characterized": True,
        "countback_through_1200_verified": True,
        "identical_request_deterministic": True,
        "vci_unit_provenance_registered": True,
        "cross_source_comparison_completed": True,
        "adjustment_semantics_verified": False,
    }

    assert derive_vci_verdict(criteria) == "qualified_with_constraints"
    assert derive_vci_verdict({**criteria, "ohlc_invariants_valid": False}) == "rejected"
    assert derive_vci_verdict({**criteria, "all_bounded_probes_executed": False}) == "unknown"
    assert derive_vci_verdict({**criteria, "adjustment_semantics_verified": True}) == "qualified"


def test_cross_source_aggregate_excludes_repeated_and_nested_probes() -> None:
    summary = _adjustment_assessment(
        [
            {
                "probe_id": "fpt-short-a",
                "symbol": "FPT",
                "included_in_unique_sample_aggregate": False,
                "overlapping_date_count": 8,
                "differing_ohlcv_row_count": 8,
                "price_ratio_vci_over_kbs": {},
            },
            {
                "probe_id": "fpt-count-1000",
                "symbol": "FPT",
                "included_in_unique_sample_aggregate": False,
                "overlapping_date_count": 1000,
                "differing_ohlcv_row_count": 900,
                "price_ratio_vci_over_kbs": {},
            },
            {
                "probe_id": "fpt-count-1200",
                "symbol": "FPT",
                "included_in_unique_sample_aggregate": True,
                "overlapping_date_count": 1200,
                "differing_ohlcv_row_count": 1078,
                "price_ratio_vci_over_kbs": {"close": {"median": 0.9998}},
            },
            {
                "probe_id": "abr-kbs-failed",
                "symbol": "ABR",
                "included_in_unique_sample_aggregate": True,
                "overlapping_date_count": 258,
                "differing_ohlcv_row_count": 32,
                "price_ratio_vci_over_kbs": {"close": {"median": 1.0}},
            },
        ]
    )

    assert summary["overlapping_rows_compared"] == 1458
    assert summary["rows_with_any_kbs_vci_ohlcv_difference"] == 1110
    assert summary["probe_weighted_overlapping_rows"] == 2466
    assert summary["aggregate_probe_ids"] == ["fpt-count-1200", "abr-kbs-failed"]
    assert summary["aggregate_symbols_unique"]


def test_offline_fake_qualification_records_all_probes_and_no_normalized_output(tmp_path) -> None:
    storage = DataStorage(tmp_path / "data")
    storage.ensure_layout()
    plan, state = _campaign_files(storage)
    provider = FakeVciProvider()
    run_id = "20260806T030000Z-qualify-vci-source-000001"

    artifacts = execute_vci_source_qualification(
        provider=provider,
        storage=storage,
        report_root=tmp_path / "reports" / "data_quality" / "source_qualification" / "vci",
        plan=plan,
        state=state,
        campaign_plan_path=storage.daily_campaign_plan_path(plan.campaign_id),
        campaign_state_path=storage.daily_campaign_state_path(plan.campaign_id),
        forensic_report_path=None,
        parent_run_id=run_id,
        started_at_utc=FIXED_NOW,
        sanitize_provider_error=str,
        max_retry_attempts=2,
        provider_sleep_seconds=2.1,
    )

    assert provider.call_count == len(VCI_QUALIFICATION_PROBES) == 11
    assert artifacts.provider_call_count == 11
    assert artifacts.payload["live_probe_count_executed"] == 11
    assert artifacts.payload["final_verdict"] == "rejected"
    assert artifacts.payload["scoped_verdict"] == "rejected_for_canonical_daily_ohlcv"
    assert not artifacts.payload["blanket_provider_usability_claim"]
    assert (
        artifacts.payload["phase_2_4_2b_recommendation"]["recommendation_class"]
        == "diagnostic_evidence_only"
    )
    assert artifacts.json_path.exists()
    assert artifacts.markdown_path.exists()
    assert len(list(storage.manifest_root.glob(f"{run_id}-*.json"))) == 11
    assert not list(storage.normalized_root.glob("**/*.parquet"))


def test_pre_start_ohlc_violation_does_not_fail_requested_window_validation(tmp_path) -> None:
    storage = DataStorage(tmp_path / "data")
    storage.ensure_layout()
    plan, state = _campaign_files(storage)
    provider = PreStartInvalidFakeVciProvider()
    run_id = "20260806T030000Z-qualify-vci-source-000002"

    artifacts = execute_vci_source_qualification(
        provider=provider,
        storage=storage,
        report_root=tmp_path / "reports" / "data_quality" / "source_qualification" / "vci",
        plan=plan,
        state=state,
        campaign_plan_path=storage.daily_campaign_plan_path(plan.campaign_id),
        campaign_state_path=storage.daily_campaign_state_path(plan.campaign_id),
        forensic_report_path=None,
        parent_run_id=run_id,
        started_at_utc=FIXED_NOW,
        sanitize_provider_error=str,
        max_retry_attempts=2,
        provider_sleep_seconds=2.1,
    )

    short_probe = next(
        item for item in artifacts.payload["probes"] if item["probe_id"] == "fpt-short-a"
    )
    assert short_probe["response"]["blocking_ohlc_check_count"] > 0
    assert short_probe["response"]["request_window_blocking_ohlc_check_count"] == 0
    assert short_probe["response"]["duplicate_date_count"] == 1
    assert short_probe["response"]["request_window_duplicate_date_count"] == 0
    assert short_probe["execution_status"] == "success"


def test_nonempty_response_with_empty_requested_window_is_blocking(tmp_path) -> None:
    storage = DataStorage(tmp_path / "data")
    storage.ensure_layout()
    plan, state = _campaign_files(storage)
    provider = EmptyWindowFakeVciProvider()
    run_id = "20260806T030000Z-qualify-vci-source-000003"

    artifacts = execute_vci_source_qualification(
        provider=provider,
        storage=storage,
        report_root=tmp_path / "reports" / "data_quality" / "source_qualification" / "vci",
        plan=plan,
        state=state,
        campaign_plan_path=storage.daily_campaign_plan_path(plan.campaign_id),
        campaign_state_path=storage.daily_campaign_state_path(plan.campaign_id),
        forensic_report_path=None,
        parent_run_id=run_id,
        started_at_utc=FIXED_NOW,
        sanitize_provider_error=str,
        max_retry_attempts=2,
        provider_sleep_seconds=2.1,
    )

    acl_probe = next(
        item for item in artifacts.payload["probes"] if item["probe_id"] == "acl-kbs-failed"
    )
    assert acl_probe["response"]["raw_row_count"] == 1
    assert acl_probe["response"]["request_window_row_count"] == 0
    assert acl_probe["execution_status"] == "failed"
    assert not artifacts.payload["qualification_criteria"][
        "requested_window_observations_match_probe_expectation"
    ]


def test_workflow_defaults_to_dry_run_without_provider_or_manifest(tmp_path) -> None:
    settings = AppSettings(
        _env_file=None,
        data_dir=tmp_path / "data",
        report_dir=tmp_path / "reports",
        provider_sleep_seconds=0,
    )
    workflow = DataWorkflow(settings)
    plan, _state = _campaign_files(workflow.storage)
    state_path = workflow.storage.daily_campaign_state_path(plan.campaign_id)
    state_before = state_path.read_bytes()

    result = workflow.qualify_vci_source(campaign_id=plan.campaign_id)

    assert result.manifest.status == "dry_run"
    assert result.manifest.dry_run
    assert result.manifest.parameters["probe_count"] == 11
    assert result.manifest.parameters["projected_wrapper_attempts"] == 22
    assert result.manifest_path is None
    assert state_path.read_bytes() == state_before
