from __future__ import annotations

import json
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

from hose_quant import cli
from hose_quant.config import PROJECT_ROOT, AppSettings
from hose_quant.data.campaigns import DailyCampaignManager
from hose_quant.data.manifests import build_manifest, write_manifest
from hose_quant.data.models import (
    CampaignAcceptanceStatus,
    CampaignReceiptOrigin,
    CampaignTaskStatus,
    DailyCampaignReadinessPolicy,
    DailyCampaignReceipt,
    DailyCampaignState,
    DailyCoverageConfig,
)
from hose_quant.data.normalizers import normalize_daily_ohlcv
from hose_quant.data.storage import DataStorage
from hose_quant.data.unit_provenance import (
    SOURCE_SPECIFIC_PROVENANCE_COLUMNS,
    VNSTOCK_KBS_DAILY_UNIT_PROVENANCE,
    resolve_daily_unit_policy,
)
from hose_quant.data.workflows import DataWorkflow

FIXED_NOW = datetime(2026, 8, 5, 2, 0, tzinfo=UTC)


def _settings(tmp_path: Path, *, max_tasks: int = 4) -> AppSettings:
    return AppSettings(  # type: ignore[call-arg]
        _env_file=None,
        data_dir=tmp_path / "data",
        report_dir=tmp_path / "reports",
        provider_sleep_seconds=0,
        max_retry_attempts=2,
        max_live_provider_calls=max_tasks * 2,
        campaign_max_tasks_per_run=max_tasks,
        daily_coverage_stale_after_days=7,
    )


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


def _universe_snapshot(symbols: list[str]) -> pd.DataFrame:
    rows = [
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
        for index, symbol in enumerate(symbols, start=1)
    ]
    rows.append(
        {
            "provider": "vnstock",
            "exchange": "HOSE",
            "symbol": "CFPT2601",
            "organ_name": "Covered warrant",
            "english_organ_name": "Covered warrant",
            "security_type": "cw",
            "provider_id": 999,
            "snapshot_timestamp_utc": FIXED_NOW,
            "raw_exchange_field": "HOSE",
            "raw_type_field": "cw",
        }
    )
    return pd.DataFrame(rows)


def _write_universe(storage: DataStorage, symbols: list[str]) -> Path:
    return storage.write_parquet(
        _universe_snapshot(symbols),
        storage.normalized_universe_path(FIXED_NOW.date(), "universe-source-run"),
    )


class CampaignProvider:
    def __init__(
        self,
        *,
        empty_symbols: set[str] | None = None,
        fail_once: bool = False,
    ) -> None:
        self.call_count = 0
        self.empty_symbols = empty_symbols or set()
        self.fail_once = fail_once
        self.requests: list[tuple[str, date, date]] = []

    def daily_unit_provenance(self):  # type: ignore[no-untyped-def]
        return VNSTOCK_KBS_DAILY_UNIT_PROVENANCE

    def fetch_daily_ohlcv(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        self.call_count += 1
        self.requests.append((symbol, start, end))
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("temporary provider failure")
        if symbol in self.empty_symbols:
            return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
        return _raw_daily(start, end)


def _init_campaign(
    workflow: DataWorkflow,
    *,
    campaign_id: str,
    start: date,
    end: date,
    chunk_days: int,
) -> None:
    result = workflow.init_daily_campaign(
        campaign_id=campaign_id,
        snapshot_date=FIXED_NOW.date(),
        start=start,
        end=end,
        chunk_calendar_days=chunk_days,
    )
    assert result.manifest.status == "success"


def test_campaign_resumes_empty_tasks_audits_and_assembles_idempotently(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, max_tasks=2)
    provider = CampaignProvider(empty_symbols={"HPG"})
    workflow = DataWorkflow(settings, provider=provider)  # type: ignore[arg-type]
    _write_universe(workflow.storage, ["FPT", "HPG"])
    _init_campaign(
        workflow,
        campaign_id="hose-daily-test",
        start=date(2026, 7, 1),
        end=date(2026, 7, 10),
        chunk_days=5,
    )

    first = workflow.run_daily_campaign(campaign_id="hose-daily-test", max_tasks=2)
    assert first.manifest.status == "success"
    assert first.manifest.provider_call_count == 2
    assert first.manifest.row_counts["campaign_complete_tasks"] == 2
    assert first.manifest.row_counts["campaign_pending_tasks"] == 2

    second = workflow.run_daily_campaign(campaign_id="hose-daily-test", max_tasks=2)
    assert second.manifest.status == "success"
    assert second.manifest.provider_call_count == 2
    assert second.manifest.row_counts["campaign_empty_tasks"] == 2
    assert provider.call_count == 4

    resumed = workflow.run_daily_campaign(campaign_id="hose-daily-test", max_tasks=2)
    assert resumed.manifest.status == "success"
    assert resumed.manifest.row_counts["tasks_selected"] == 0
    assert resumed.manifest.provider_call_count == 0
    assert provider.call_count == 4

    audit = workflow.audit_daily_campaign(
        campaign_id="hose-daily-test",
        config=DailyCoverageConfig(
            min_history_observations=1,
            min_span_coverage_ratio=0.5,
            stale_after_calendar_days=7,
            max_zero_volume_frequency=0.2,
        ),
    )
    assert audit.manifest.status == "success"
    assert audit.manifest.parameters["assembly_ready"] is True
    assert audit.manifest.parameters["coverage_quality_status"] == "rejected"
    assert audit.manifest.parameters["research_readiness_status"] == "rejected"
    assert audit.manifest.parameters["canonical_candidate"] is False
    assert audit.manifest.row_counts["vnd_liquidity_usable_symbols"] == 1
    report_path = next(
        Path(path) for path in audit.manifest.output_paths if path.endswith(".json")
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"]["task_counts"]["complete"] == 2
    assert report["summary"]["task_counts"]["empty"] == 2
    assert report["summary"]["coverage"]["status_counts"] == {
        "absent": 1,
        "usable_vnd": 1,
    }
    assert report["audit_contract_version"] == "daily-campaign-audit-v2"
    assert report["readiness_assessment"]["criteria"][
        "minimum_vnd_usable_symbol_ratio"
    ] is False
    assert report["readiness_assessment"]["criteria"][
        "maximum_absent_symbol_ratio"
    ] is False

    assembled = workflow.assemble_daily_campaign(campaign_id="hose-daily-test")
    assert assembled.manifest.status == "success"
    dataset_id = assembled.manifest.parameters["assembled_dataset_id"]
    parquet_paths = [
        Path(path) for path in assembled.manifest.output_paths if path.endswith(".parquet")
    ]
    assert len(parquet_paths) == 1
    frame = pd.read_parquet(parquet_paths[0])
    assert set(frame["symbol"]) == {"FPT"}
    assert not frame.duplicated(["symbol", "date"]).any()
    assert set(frame["campaign_id"]) == {"hose-daily-test"}
    assert set(frame["assembled_dataset_id"]) == {dataset_id}
    assert frame["source_run_id"].notna().all()
    assert assembled.manifest.parameters["research_readiness_status"] == "rejected"
    assert assembled.manifest.parameters["canonical_candidate"] is False

    repeated = workflow.assemble_daily_campaign(campaign_id="hose-daily-test")
    assert repeated.manifest.status == "success"
    assert repeated.manifest.parameters["assembled_dataset_id"] == dataset_id
    assert repeated.manifest.parameters["canonical_candidate"] is False


def test_assembly_never_grants_readiness_but_accepted_audit_can_mark_candidate(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, max_tasks=2)
    provider = CampaignProvider()
    workflow = DataWorkflow(settings, provider=provider)  # type: ignore[arg-type]
    _write_universe(workflow.storage, ["FPT", "HPG"])
    _init_campaign(
        workflow,
        campaign_id="readiness-test",
        start=date(2026, 7, 6),
        end=date(2026, 7, 10),
        chunk_days=5,
    )
    completed = workflow.run_daily_campaign(campaign_id="readiness-test", max_tasks=2)
    assert completed.manifest.status == "success"

    assembled = workflow.assemble_daily_campaign(campaign_id="readiness-test")
    assert assembled.manifest.status == "success"
    assert assembled.manifest.parameters["coverage_quality_status"] == "not_assessed"
    assert assembled.manifest.parameters["research_readiness_status"] == "not_assessed"
    assert assembled.manifest.parameters["canonical_candidate"] is False

    audit = workflow.audit_daily_campaign(
        campaign_id="readiness-test",
        config=DailyCoverageConfig(
            min_history_observations=1,
            min_span_coverage_ratio=0.5,
            stale_after_calendar_days=7,
            max_zero_volume_frequency=0.2,
        ),
    )
    assert audit.manifest.status == "success"
    assert audit.manifest.parameters["coverage_quality_status"] == "accepted"
    assert audit.manifest.parameters["research_readiness_status"] == "accepted"
    assert audit.manifest.parameters["canonical_candidate"] is True

    state = DailyCampaignManager(workflow.storage).assess(
        DailyCampaignManager(workflow.storage).load_plan("readiness-test")
    )
    assert state.coverage_quality_status is CampaignAcceptanceStatus.ACCEPTED
    assert state.research_readiness_status is CampaignAcceptanceStatus.ACCEPTED
    assert state.canonical_candidate is True
    assert state.readiness_assessment is not None
    assert state.readiness_assessment.audit_run_id == audit.manifest.run_id


def test_readiness_policy_records_explicit_bounded_absence_override(tmp_path: Path) -> None:
    settings = _settings(tmp_path, max_tasks=2)
    provider = CampaignProvider(empty_symbols={"HPG"})
    workflow = DataWorkflow(settings, provider=provider)  # type: ignore[arg-type]
    _write_universe(workflow.storage, ["FPT", "HPG"])
    _init_campaign(
        workflow,
        campaign_id="bounded-absence-test",
        start=date(2026, 7, 6),
        end=date(2026, 7, 10),
        chunk_days=5,
    )
    workflow.run_daily_campaign(campaign_id="bounded-absence-test", max_tasks=2)

    audit = workflow.audit_daily_campaign(
        campaign_id="bounded-absence-test",
        config=DailyCoverageConfig(
            min_history_observations=1,
            min_span_coverage_ratio=0.5,
            stale_after_calendar_days=7,
            max_zero_volume_frequency=0.2,
        ),
        readiness_policy=DailyCampaignReadinessPolicy(
            min_vnd_usable_symbol_ratio=0.5,
            max_absent_symbol_ratio=0.5,
        ),
    )
    assert audit.manifest.parameters["research_readiness_status"] == "accepted"
    assert audit.manifest.parameters["readiness_policy"] == {
        "policy_version": "campaign-research-readiness-policy-v1",
        "research_scope": "raw_ohlcv_and_vnd_liquidity",
        "min_vnd_usable_symbol_ratio": 0.5,
        "max_absent_symbol_ratio": 0.5,
        "require_common_vnd_date_overlap": True,
    }


def test_failed_task_requires_explicit_retry_and_then_completes(tmp_path: Path) -> None:
    settings = _settings(tmp_path, max_tasks=1)
    provider = CampaignProvider(fail_once=True)
    workflow = DataWorkflow(settings, provider=provider)  # type: ignore[arg-type]
    _write_universe(workflow.storage, ["FPT"])
    _init_campaign(
        workflow,
        campaign_id="retry-test",
        start=date(2026, 7, 6),
        end=date(2026, 7, 10),
        chunk_days=5,
    )

    failed = workflow.run_daily_campaign(campaign_id="retry-test", max_tasks=1)
    assert failed.manifest.status == "failed"
    assert failed.manifest.row_counts["campaign_failed_tasks"] == 1

    skipped = workflow.run_daily_campaign(campaign_id="retry-test", max_tasks=1)
    assert skipped.manifest.status == "success"
    assert skipped.manifest.row_counts["tasks_selected"] == 0
    assert provider.call_count == 1

    retried = workflow.run_daily_campaign(
        campaign_id="retry-test",
        max_tasks=1,
        retry_failed=True,
    )
    assert retried.manifest.status == "success"
    assert retried.manifest.row_counts["campaign_complete_tasks"] == 1
    assert provider.call_count == 2


def test_campaign_reconciles_child_manifest_when_receipt_was_not_written(tmp_path: Path) -> None:
    settings = _settings(tmp_path, max_tasks=1)
    provider = CampaignProvider()
    workflow = DataWorkflow(settings, provider=provider)  # type: ignore[arg-type]
    _write_universe(workflow.storage, ["FPT"])
    _init_campaign(
        workflow,
        campaign_id="reconcile-test",
        start=date(2026, 7, 6),
        end=date(2026, 7, 10),
        chunk_days=5,
    )
    manager = DailyCampaignManager(workflow.storage)
    plan = manager.load_plan("reconcile-test")
    task = plan.tasks[0]

    child = workflow.backfill_daily(
        symbols=[task.symbol],
        start=task.start_date,
        end=task.end_date,
        chunk_calendar_days=5,
        campaign_id=plan.campaign_id,
        campaign_task_id=task.task_id,
        allow_empty_success=True,
    )
    assert child.manifest.status == "success"
    assert workflow.storage.daily_campaign_receipt_paths(plan.campaign_id) == []

    state = manager.assess(plan)
    assert state.task_counts["complete"] == 1
    assert len(workflow.storage.daily_campaign_receipt_paths(plan.campaign_id)) == 1


def test_legacy_verified_run_can_be_adopted_by_structural_validation(tmp_path: Path) -> None:
    settings = _settings(tmp_path, max_tasks=1)
    workflow = DataWorkflow(settings)
    _write_universe(workflow.storage, ["FPT"])
    start = date(2026, 7, 6)
    end = date(2026, 7, 10)
    _init_campaign(
        workflow,
        campaign_id="legacy-adopt-test",
        start=start,
        end=end,
        chunk_days=5,
    )
    source_run_id = "legacy-verified-run"
    frame = _verified_daily("FPT", start, end)
    source_path = workflow.storage.write_parquet(
        frame,
        workflow.storage.normalized_daily_path("FPT", source_run_id),
    )
    manifest = build_manifest(
        run_id=source_run_id,
        command="data backfill-daily",
        started_at_utc=FIXED_NOW,
        finished_at_utc=FIXED_NOW,
        status="success",
        symbols=["FPT"],
        exchange="HOSE",
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        row_counts={"normalized": len(frame), "chunks_empty": 0},
        output_paths=[source_path],
        unit_provenance=resolve_daily_unit_policy(frame),
    )
    write_manifest(manifest, workflow.storage.manifest_root)

    adopted = workflow.adopt_daily_run(
        campaign_id="legacy-adopt-test",
        daily_run_id=source_run_id,
    )
    assert adopted.manifest.status == "success"
    state = DailyCampaignState.model_validate_json(
        workflow.storage.daily_campaign_state_path("legacy-adopt-test").read_text()
    )
    assert state.tasks[0].status is CampaignTaskStatus.COMPLETE
    assert "legacy_contract_structurally_validated" in state.tasks[0].reason_codes


def test_incompatible_provenance_is_retained_and_assembly_is_refused(tmp_path: Path) -> None:
    settings = _settings(tmp_path, max_tasks=1)
    workflow = DataWorkflow(settings)
    _write_universe(workflow.storage, ["FPT"])
    start = date(2026, 7, 6)
    end = date(2026, 7, 10)
    _init_campaign(
        workflow,
        campaign_id="incompatible-test",
        start=start,
        end=end,
        chunk_days=5,
    )
    source_run_id = "legacy-unverified-run"
    frame = _verified_daily("FPT", start, end).drop(
        columns=list(SOURCE_SPECIFIC_PROVENANCE_COLUMNS)
    )
    source_path = workflow.storage.write_parquet(
        frame,
        workflow.storage.normalized_daily_path("FPT", source_run_id),
    )
    manifest = build_manifest(
        run_id=source_run_id,
        command="data backfill-daily",
        started_at_utc=FIXED_NOW,
        finished_at_utc=FIXED_NOW,
        status="success",
        symbols=["FPT"],
        exchange="HOSE",
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        row_counts={"normalized": len(frame), "chunks_empty": 0},
        output_paths=[source_path],
        unit_provenance=resolve_daily_unit_policy(frame),
    )
    write_manifest(manifest, workflow.storage.manifest_root)

    adopted = workflow.adopt_daily_run(
        campaign_id="incompatible-test",
        daily_run_id=source_run_id,
    )
    assert adopted.manifest.status == "failed"
    state = DailyCampaignState.model_validate_json(
        workflow.storage.daily_campaign_state_path("incompatible-test").read_text()
    )
    assert state.task_counts["incompatible"] == 1
    assert source_run_id in state.tasks[0].attempt_run_ids

    assembled = workflow.assemble_daily_campaign(campaign_id="incompatible-test")
    assert assembled.manifest.status == "failed"
    assert workflow.storage.assembled_daily_paths("incompatible-test", "missing") == []
    assert list(workflow.storage.assembled_root.glob("**/*.parquet")) == []


def test_multiple_compatible_task_sources_are_not_selected_silently(tmp_path: Path) -> None:
    settings = _settings(tmp_path, max_tasks=1)
    workflow = DataWorkflow(settings)
    _write_universe(workflow.storage, ["FPT"])
    start = date(2026, 7, 6)
    end = date(2026, 7, 10)
    campaign_id = "multiple-source-test"
    _init_campaign(
        workflow,
        campaign_id=campaign_id,
        start=start,
        end=end,
        chunk_days=5,
    )
    manager = DailyCampaignManager(workflow.storage)
    plan = manager.load_plan(campaign_id)
    task = plan.tasks[0]

    for source_run_id in ["compatible-source-a", "compatible-source-b"]:
        frame = _verified_daily("FPT", start, end)
        source_path = workflow.storage.write_parquet(
            frame,
            workflow.storage.normalized_daily_path("FPT", source_run_id),
        )
        manifest = build_manifest(
            run_id=source_run_id,
            command="data backfill-daily",
            started_at_utc=FIXED_NOW,
            finished_at_utc=FIXED_NOW,
            status="success",
            symbols=["FPT"],
            exchange="HOSE",
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            resolution="1D",
            row_counts={"normalized": len(frame), "chunks_empty": 0},
            output_paths=[source_path],
            parameters={
                "price_adjustment_semantics": "unknown_provider_adjustment_flag",
                "expected_adjusted_flag": None,
            },
            unit_provenance=resolve_daily_unit_policy(frame),
            data_contract_versions={"normalized_daily": "normalized-daily-v2"},
        )
        write_manifest(manifest, workflow.storage.manifest_root)
        manager.record_receipt(
            DailyCampaignReceipt(
                campaign_contract_version=plan.campaign_contract_version,
                campaign_id=campaign_id,
                task_id=task.task_id,
                source_run_id=source_run_id,
                origin=CampaignReceiptOrigin.ADOPTED_RUN,
            )
        )

    state = manager.assess(plan)
    assert state.task_counts["incompatible"] == 1
    assert state.tasks[0].selected_run_id is None
    assert state.tasks[0].reason_codes == ["multiple_compatible_successful_sources_for_task"]
    assembled = workflow.assemble_daily_campaign(campaign_id=campaign_id)
    assert assembled.manifest.status == "failed"


def test_phase_23_generated_outputs_are_ignored_by_git() -> None:
    paths = [
        "data/campaigns/vnstock/daily/campaign_id=test/plan.json",
        "data/campaigns/vnstock/daily/campaign_id=test/receipts/task/run.json",
        "data/assembled/vnstock/daily/campaign_id=test/dataset_id=v1/symbol=FPT.parquet",
        "reports/data_quality/campaigns/test/audit.json",
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


def test_campaign_cli_dry_run_is_offline_but_live_run_requires_key(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    settings = _settings(tmp_path, max_tasks=1)
    workflow = DataWorkflow(settings)
    _write_universe(workflow.storage, ["FPT"])
    _init_campaign(
        workflow,
        campaign_id="cli-test",
        start=date(2026, 7, 6),
        end=date(2026, 7, 10),
        chunk_days=5,
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    dry_exit = cli.main(
        ["data", "run-daily-campaign", "--campaign-id", "cli-test", "--dry-run"]
    )
    dry_output = capsys.readouterr()
    assert dry_exit == 0
    assert "Status: dry_run" in dry_output.out
    assert "Plan selected_task_ids" in dry_output.out

    live_exit = cli.main(["data", "run-daily-campaign", "--campaign-id", "cli-test"])
    live_output = capsys.readouterr()
    assert live_exit == 2
    assert "VNSTOCK_API_KEY is required" in live_output.err

    audit_exit = cli.main(
        [
            "data",
            "audit-daily-campaign",
            "--campaign-id",
            "cli-test",
            "--min-history-observations",
            "1",
            "--min-vnd-usable-symbol-ratio",
            "0.5",
            "--max-absent-symbol-ratio",
            "0.5",
        ]
    )
    audit_output = capsys.readouterr()
    assert audit_exit == 0
    assert "Campaign complete: False" in audit_output.out
    assert "Coverage-quality status: rejected" in audit_output.out
    assert "Research-readiness status: rejected" in audit_output.out
    assert "Canonical candidate: False" in audit_output.out
