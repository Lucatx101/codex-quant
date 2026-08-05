from __future__ import annotations

import fcntl
import hashlib
import json
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd

from hose_quant.data.contracts import (
    ASSEMBLED_DAILY_CONTRACT_VERSION,
    DAILY_CAMPAIGN_AUDIT_CONTRACT_VERSION,
    DAILY_CAMPAIGN_CONTRACT_VERSION,
    DAILY_CAMPAIGN_READINESS_CONTRACT_VERSION,
    DAILY_CAMPAIGN_READINESS_POLICY_VERSION,
    DAILY_CAMPAIGN_STATE_CONTRACT_VERSION,
    NORMALIZED_DAILY_CONTRACT_VERSION,
)
from hose_quant.data.coverage import audit_daily_coverage, summarize_daily_coverage
from hose_quant.data.feature_inputs import build_daily_panel
from hose_quant.data.models import (
    CampaignAcceptanceStatus,
    CampaignReceiptOrigin,
    CampaignTaskStatus,
    DailyCampaignPlan,
    DailyCampaignReadinessAssessment,
    DailyCampaignReadinessPolicy,
    DailyCampaignReceipt,
    DailyCampaignState,
    DailyCampaignTask,
    DailyCampaignTaskAssessment,
    DailyCoverageConfig,
    DailyCoverageStatus,
    DatasetManifest,
    ValidationResult,
    utc_now,
)
from hose_quant.data.storage import DataStorage
from hose_quant.data.unit_provenance import resolve_daily_unit_policy
from hose_quant.data.validators import (
    has_blocking_errors,
    validate_assembled_daily,
    validate_daily_ohlcv,
    validate_daily_panel,
)

RESOLVED_TASK_STATUSES = {
    CampaignTaskStatus.COMPLETE,
    CampaignTaskStatus.EMPTY,
}
CAMPAIGN_KNOWN_RISKS = [
    "current_snapshot_is_not_historical_point_in_time_membership",
    "price_adjustment_semantics_unverified",
    "corporate_action_completeness_unverified",
    "weekday_calendar_omits_vietnam_holidays_closures_and_halts",
    "provider_wrapper_call_count_may_exclude_internal_http_retries",
]


class CampaignCompatibilityError(ValueError):
    """Raised when campaign evidence cannot be combined safely."""


class CampaignIncompleteError(ValueError):
    """Raised when assembly is requested before every task is resolved."""


def campaign_task_id(symbol: str, start: date, end: date) -> str:
    return f"{symbol.upper()}__{start.isoformat()}__{end.isoformat()}"


def build_campaign_tasks(
    *,
    symbols: list[str],
    chunks: list[tuple[date, date]],
) -> list[DailyCampaignTask]:
    return [
        DailyCampaignTask(
            task_id=campaign_task_id(symbol, start, end),
            symbol=symbol,
            start_date=start,
            end_date=end,
        )
        for symbol in sorted(symbols)
        for start, end in chunks
    ]


class DailyCampaignManager:
    def __init__(self, storage: DataStorage) -> None:
        self.storage = storage

    @contextmanager
    def lock(self, campaign_id: str) -> Iterator[None]:
        lock_path = self.storage.daily_campaign_dir(campaign_id) / "operation.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError(
                    f"Campaign {campaign_id} already has another active operation."
                ) from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def write_plan(self, plan: DailyCampaignPlan) -> Path:
        path = self.storage.daily_campaign_plan_path(plan.campaign_id)
        if path.exists():
            raise ValueError(f"Campaign {plan.campaign_id} already exists and is immutable.")
        _write_model_atomic(path, plan.model_dump(mode="json"))
        return path

    def load_plan(self, campaign_id: str) -> DailyCampaignPlan:
        path = self.storage.daily_campaign_plan_path(campaign_id)
        if not path.exists():
            raise ValueError(f"Campaign plan not found: {campaign_id}.")
        plan = DailyCampaignPlan.model_validate_json(path.read_text(encoding="utf-8"))
        if plan.campaign_contract_version != DAILY_CAMPAIGN_CONTRACT_VERSION:
            raise ValueError(
                f"Unsupported campaign contract: {plan.campaign_contract_version}."
            )
        if plan.normalized_daily_contract_version != NORMALIZED_DAILY_CONTRACT_VERSION:
            raise ValueError(
                "Unsupported normalized daily campaign input contract: "
                f"{plan.normalized_daily_contract_version}."
            )
        return plan

    def record_receipt(self, receipt: DailyCampaignReceipt) -> Path:
        path = self.storage.daily_campaign_receipt_path(
            campaign_id=receipt.campaign_id,
            task_id=receipt.task_id,
            run_id=receipt.source_run_id,
        )
        if path.exists():
            existing = DailyCampaignReceipt.model_validate_json(
                path.read_text(encoding="utf-8")
            )
            if existing != receipt:
                raise ValueError(f"Receipt collision at {path}.")
            return path
        _write_model_atomic(path, receipt.model_dump(mode="json"))
        return path

    def reconcile_campaign_manifests(self, plan: DailyCampaignPlan) -> list[Path]:
        task_ids = {task.task_id for task in plan.tasks}
        written: list[Path] = []
        for path in sorted(self.storage.manifest_root.glob("*.json")):
            manifest = DatasetManifest.model_validate_json(path.read_text(encoding="utf-8"))
            if manifest.command != "data backfill-daily":
                continue
            if manifest.parameters.get("campaign_id") != plan.campaign_id:
                continue
            task_id = manifest.parameters.get("campaign_task_id")
            if not isinstance(task_id, str) or task_id not in task_ids:
                raise ValueError(
                    f"Campaign manifest {manifest.run_id} has an unknown task ID."
                )
            receipt = DailyCampaignReceipt(
                campaign_contract_version=DAILY_CAMPAIGN_CONTRACT_VERSION,
                campaign_id=plan.campaign_id,
                task_id=task_id,
                source_run_id=manifest.run_id,
                origin=CampaignReceiptOrigin.CAMPAIGN_RUN,
                recorded_at_utc=manifest.finished_at_utc,
            )
            written.append(self.record_receipt(receipt))
        return written

    def load_receipts(
        self,
        plan: DailyCampaignPlan,
    ) -> dict[str, list[DailyCampaignReceipt]]:
        task_ids = {task.task_id for task in plan.tasks}
        grouped: dict[str, list[DailyCampaignReceipt]] = {task_id: [] for task_id in task_ids}
        for path in self.storage.daily_campaign_receipt_paths(plan.campaign_id):
            receipt = DailyCampaignReceipt.model_validate_json(path.read_text(encoding="utf-8"))
            if (
                receipt.campaign_id != plan.campaign_id
                or receipt.campaign_contract_version != plan.campaign_contract_version
                or receipt.task_id not in task_ids
            ):
                raise ValueError(f"Invalid campaign receipt: {path}.")
            grouped[receipt.task_id].append(receipt)
        for receipts in grouped.values():
            receipts.sort(key=lambda item: (item.recorded_at_utc, item.source_run_id))
        return grouped

    def assess(self, plan: DailyCampaignPlan) -> DailyCampaignState:
        self.reconcile_campaign_manifests(plan)
        receipts = self.load_receipts(plan)
        assessments = [
            self._assess_task(plan, task, receipts[task.task_id]) for task in plan.tasks
        ]
        observed_task_counts = Counter(item.status.value for item in assessments)
        task_counts = {
            status.value: observed_task_counts.get(status.value, 0)
            for status in CampaignTaskStatus
        }
        symbol_statuses = self._symbol_statuses(plan, assessments)
        symbol_counts = Counter(symbol_statuses.values())
        campaign_complete = all(
            item.status in RESOLVED_TASK_STATUSES for item in assessments
        )
        assembly_compatible = not any(
            item.status is CampaignTaskStatus.INCOMPATIBLE for item in assessments
        )
        assembly_ready = campaign_complete and assembly_compatible
        previous: DailyCampaignState | None = None
        state_path = self.storage.daily_campaign_state_path(plan.campaign_id)
        if state_path.exists():
            previous = DailyCampaignState.model_validate_json(
                state_path.read_text(encoding="utf-8")
            )
        state = DailyCampaignState(
            state_contract_version=DAILY_CAMPAIGN_STATE_CONTRACT_VERSION,
            campaign_contract_version=plan.campaign_contract_version,
            campaign_id=plan.campaign_id,
            task_counts=task_counts,
            symbol_counts=dict(sorted(symbol_counts.items())),
            source_run_ids=sorted(
                {
                    item.selected_run_id
                    for item in assessments
                    if item.selected_run_id is not None
                    and item.status in RESOLVED_TASK_STATUSES
                }
            ),
            campaign_complete=campaign_complete,
            assembly_compatible=assembly_compatible,
            assembly_ready=assembly_ready,
            canonical_candidate=False,
            assembled_dataset_id=None,
            tasks=assessments,
        )
        if previous is not None:
            evidence_digest = campaign_source_evidence_digest(plan, state)
            previous_assessment = previous.readiness_assessment
            if (
                previous_assessment is not None
                and previous_assessment.source_evidence_digest == evidence_digest
            ):
                state.readiness_assessment = previous_assessment
                state.coverage_quality_status = (
                    previous_assessment.coverage_quality_status
                )
                state.research_readiness_status = (
                    previous_assessment.research_readiness_status
                )
            if previous.assembled_dataset_id is not None:
                expected_id = assembled_dataset_id(plan, state)
                if (
                    previous.assembled_dataset_id == expected_id
                    and self.storage.assembled_daily_dataset_dir(
                        plan.campaign_id, expected_id
                    ).exists()
                ):
                    state.assembled_dataset_id = expected_id
        state.canonical_candidate = (
            state.research_readiness_status is CampaignAcceptanceStatus.ACCEPTED
            and state.assembled_dataset_id is not None
        )
        self.write_state(state)
        return state

    def write_state(self, state: DailyCampaignState) -> Path:
        path = self.storage.daily_campaign_state_path(state.campaign_id)
        _write_model_atomic(path, state.model_dump(mode="json"))
        return path

    def select_tasks(
        self,
        state: DailyCampaignState,
        *,
        max_tasks: int,
        retry_failed: bool,
        retry_stale: bool,
        retry_incompatible: bool,
    ) -> list[DailyCampaignTaskAssessment]:
        eligible = {CampaignTaskStatus.PENDING}
        if retry_failed:
            eligible.add(CampaignTaskStatus.FAILED)
        if retry_stale:
            eligible.add(CampaignTaskStatus.STALE)
        if retry_incompatible:
            eligible.add(CampaignTaskStatus.INCOMPATIBLE)
        return [item for item in state.tasks if item.status in eligible][:max_tasks]

    def adopt_run(
        self,
        plan: DailyCampaignPlan,
        *,
        run_id: str,
    ) -> tuple[list[Path], DailyCampaignState]:
        manifest = self.storage.read_manifest(run_id)
        if manifest is None:
            raise CampaignCompatibilityError(f"Source manifest not found: {run_id}.")
        if manifest.command != "data backfill-daily" or manifest.status != "success":
            raise CampaignCompatibilityError(
                "Only successful data backfill-daily runs can be adopted."
            )
        try:
            source_start = date.fromisoformat(str(manifest.start_date))
            source_end = date.fromisoformat(str(manifest.end_date))
        except ValueError as exc:
            raise CampaignCompatibilityError("Source run has invalid date bounds.") from exc
        covered_tasks = [
            task
            for task in plan.tasks
            if task.symbol in manifest.symbols
            and source_start <= task.start_date
            and source_end >= task.end_date
        ]
        if not covered_tasks:
            raise CampaignCompatibilityError("Source run covers no campaign tasks.")
        covered_by_symbol: dict[str, list[DailyCampaignTask]] = {}
        for task in covered_tasks:
            covered_by_symbol.setdefault(task.symbol, []).append(task)
        for symbol in manifest.symbols:
            tasks = sorted(covered_by_symbol.get(symbol, []), key=lambda item: item.start_date)
            if not tasks or tasks[0].start_date != source_start or tasks[-1].end_date != source_end:
                raise CampaignCompatibilityError(
                    f"Source range does not align to complete campaign chunks for {symbol}."
                )

        current = self.assess(plan)
        current_by_task = {item.task_id: item for item in current.tasks}
        receipts: list[DailyCampaignReceipt] = []
        incompatible: list[str] = []
        for task in covered_tasks:
            existing = current_by_task[task.task_id]
            if existing.status in RESOLVED_TASK_STATUSES:
                if existing.selected_run_id == run_id:
                    continue
                raise CampaignCompatibilityError(
                    f"Task {task.task_id} already has a different successful source."
                )
            receipt = DailyCampaignReceipt(
                campaign_contract_version=plan.campaign_contract_version,
                campaign_id=plan.campaign_id,
                task_id=task.task_id,
                source_run_id=run_id,
                origin=CampaignReceiptOrigin.ADOPTED_RUN,
            )
            assessment = self._assess_source_for_task(plan, task, receipt)
            if assessment.status not in {
                CampaignTaskStatus.COMPLETE,
                CampaignTaskStatus.EMPTY,
                CampaignTaskStatus.STALE,
            }:
                incompatible.append(
                    f"{task.task_id} ({', '.join(assessment.reason_codes)})"
                )
            receipts.append(receipt)
        paths = [self.record_receipt(receipt) for receipt in receipts]
        state = self.assess(plan)
        if incompatible:
            raise CampaignCompatibilityError(
                f"Run {run_id} has {len(incompatible)} incompatible campaign tasks; "
                f"first: {incompatible[0]}. Evidence was retained but not assembled."
            )
        return paths, state

    def audit(
        self,
        plan: DailyCampaignPlan,
        *,
        audit_run_id: str,
        coverage_config: DailyCoverageConfig,
        readiness_policy: DailyCampaignReadinessPolicy,
        json_path: Path,
        markdown_path: Path,
    ) -> tuple[DailyCampaignState, pd.DataFrame, dict[str, Any], tuple[Path, Path]]:
        state = self.assess(plan)
        complete_symbols = self._complete_symbols(plan, state.tasks)
        source_rows, _input_paths = self.source_rows(
            plan,
            state,
            symbols=complete_symbols,
        )
        duplicate_count = (
            int(source_rows.duplicated(["symbol", "date"]).sum())
            if not source_rows.empty
            else 0
        )
        coverage_input = source_rows.drop(
            columns=["source_run_id", "source_normalized_path"], errors="ignore"
        )
        if coverage_input.empty:
            coverage_input = pd.DataFrame(
                columns=["symbol", "date", "open", "high", "low", "close", "volume"]
            )
        coverage = audit_daily_coverage(
            coverage_input,
            current_universe_symbols=set(plan.symbols),
            requested_symbols=complete_symbols,
            universe_snapshot_date=plan.universe_snapshot_date,
            daily_run_id=f"campaign:{plan.campaign_id}",
            start=plan.start_date,
            end=plan.end_date,
            config=coverage_config,
        )
        coverage_summary = summarize_daily_coverage(coverage)
        virtual_source_run_ids = sorted(
            source_rows["source_run_id"].dropna().astype(str).unique().tolist()
        ) if not source_rows.empty else []
        unit_policy = resolve_daily_unit_policy(coverage_input)
        readiness = evaluate_campaign_readiness(
            plan,
            state,
            coverage,
            audit_run_id=audit_run_id,
            coverage_config=coverage_config,
            readiness_policy=readiness_policy,
            source_row_count=len(source_rows),
            duplicate_symbol_date_count=duplicate_count,
            task_range_gap_count=0,
            task_range_overlap_count=0,
            vnd_traded_value_permitted=unit_policy.can_compute_vnd,
        )
        state.readiness_assessment = readiness
        state.coverage_quality_status = readiness.coverage_quality_status
        state.research_readiness_status = readiness.research_readiness_status
        state.canonical_candidate = (
            readiness.research_readiness_status is CampaignAcceptanceStatus.ACCEPTED
            and state.assembled_dataset_id is not None
        )
        self.write_state(state)
        summary: dict[str, Any] = {
            "campaign_id": plan.campaign_id,
            "campaign_contract_version": plan.campaign_contract_version,
            "audit_contract_version": DAILY_CAMPAIGN_AUDIT_CONTRACT_VERSION,
            "universe_symbol_count": len(plan.symbols),
            "task_count": len(plan.tasks),
            "task_counts": state.task_counts,
            "symbol_counts": state.symbol_counts,
            "complete_symbol_count": len(complete_symbols),
            "resolved_task_source_run_count": len(state.source_run_ids),
            "resolved_task_source_run_ids": state.source_run_ids,
            "virtual_source_run_count": len(virtual_source_run_ids),
            "virtual_source_run_ids": virtual_source_run_ids,
            "virtual_source_row_count": len(source_rows),
            "duplicate_symbol_date_count": duplicate_count,
            "task_range_gap_count": 0,
            "task_range_overlap_count": 0,
            "campaign_complete": state.campaign_complete,
            "assembly_compatible": state.assembly_compatible,
            "assembly_ready": state.assembly_ready,
            "coverage_quality_status": state.coverage_quality_status.value,
            "research_readiness_status": state.research_readiness_status.value,
            "canonical_candidate": state.canonical_candidate,
            "assembled_dataset_id": state.assembled_dataset_id,
            "unit_provenance_status": unit_policy.provenance_status.value,
            "vnd_traded_value_permitted": unit_policy.vnd_traded_value_permitted,
            "readiness": readiness.model_dump(mode="json"),
            "coverage": coverage_summary,
            "known_risks": CAMPAIGN_KNOWN_RISKS,
        }
        paths = write_campaign_audit_report(
            plan,
            state,
            coverage,
            summary=summary,
            json_path=json_path,
            markdown_path=markdown_path,
        )
        return state, coverage, summary, paths

    def assemble(
        self,
        plan: DailyCampaignPlan,
    ) -> tuple[
        str,
        pd.DataFrame,
        list[Path],
        list[Path],
        list[ValidationResult],
        DailyCampaignState,
    ]:
        state = self.assess(plan)
        if not state.assembly_ready:
            unresolved = len(plan.tasks) - sum(
                state.task_counts.get(status.value, 0) for status in RESOLVED_TASK_STATUSES
            )
            raise CampaignIncompleteError(
                f"Campaign {plan.campaign_id} has {unresolved} unresolved tasks; assembly refused."
            )
        source_rows, input_paths = self.source_rows(
            plan,
            state,
            symbols=set(plan.symbols),
        )
        if source_rows.empty:
            raise CampaignCompatibilityError("Resolved campaign contains no daily observations.")
        if source_rows.duplicated(["symbol", "date"]).any():
            raise CampaignCompatibilityError(
                "Compatible campaign sources overlap on symbol/date; assembly refused."
            )
        lineage = source_rows[
            ["symbol", "date", "source_run_id", "source_normalized_path"]
        ].copy()
        lineage["date"] = pd.to_datetime(lineage["date"], errors="coerce").dt.normalize()
        normalized = source_rows.drop(
            columns=["source_run_id", "source_normalized_path"], errors="ignore"
        )
        panel = build_daily_panel(
            normalized,
            symbols=plan.symbols,
            start=plan.start_date,
            end=plan.end_date,
        )
        validation_results = validate_daily_panel(
            panel,
            expected_source_row_count=len(normalized),
        )
        dataset_id = assembled_dataset_id(plan, state)
        assembled = panel.merge(
            lineage,
            on=["symbol", "date"],
            how="left",
            validate="one_to_one",
        )
        assembled["assembly_contract_version"] = ASSEMBLED_DAILY_CONTRACT_VERSION
        assembled["campaign_id"] = plan.campaign_id
        assembled["assembled_dataset_id"] = dataset_id
        validation_results.extend(
            validate_assembled_daily(
                assembled,
                expected_row_count=len(normalized),
                expected_campaign_id=plan.campaign_id,
                expected_dataset_id=dataset_id,
            )
        )
        if has_blocking_errors(validation_results):
            raise CampaignCompatibilityError(
                "Assembled daily validation failed; no dataset was published."
            )

        final_dir = self.storage.assembled_daily_dataset_dir(plan.campaign_id, dataset_id)
        output_paths: list[Path]
        if final_dir.exists():
            output_paths = self.storage.assembled_daily_paths(plan.campaign_id, dataset_id)
            if not output_paths:
                raise CampaignCompatibilityError(
                    f"Existing assembled dataset is incomplete: {final_dir}."
                )
            metadata_path = final_dir / "dataset.json"
            if not metadata_path.exists():
                raise CampaignCompatibilityError(
                    f"Existing assembled dataset has no metadata: {final_dir}."
                )
            published = pd.concat(
                [pd.read_parquet(path) for path in output_paths],
                ignore_index=True,
            ).sort_values(["symbol", "date"], kind="stable").reset_index(drop=True)
            expected = assembled.sort_values(
                ["symbol", "date"], kind="stable"
            ).reset_index(drop=True)
            try:
                pd.testing.assert_frame_equal(
                    published,
                    expected,
                    check_dtype=False,
                    check_like=False,
                )
            except AssertionError as exc:
                raise CampaignCompatibilityError(
                    f"Existing assembled dataset differs from deterministic source: {final_dir}."
                ) from exc
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            expected_metadata = {
                "assembly_contract_version": ASSEMBLED_DAILY_CONTRACT_VERSION,
                "campaign_id": plan.campaign_id,
                "assembled_dataset_id": dataset_id,
                "row_count": len(assembled),
                "symbol_count": int(assembled["symbol"].nunique()),
                "source_run_ids": state.source_run_ids,
            }
            if any(metadata.get(key) != value for key, value in expected_metadata.items()):
                raise CampaignCompatibilityError(
                    f"Existing assembled dataset metadata is incompatible: {final_dir}."
                )
            output_paths.append(metadata_path)
        else:
            staging = final_dir.parent / f".staging-{dataset_id}-{uuid4().hex}"
            staging.mkdir(parents=True, exist_ok=False)
            output_paths = []
            for symbol, group in assembled.groupby("symbol", sort=True):
                path = staging / f"symbol={symbol}.parquet"
                group.reset_index(drop=True).to_parquet(path, index=False)
                output_paths.append(path)
            metadata_path = staging / "dataset.json"
            _write_model_atomic(
                metadata_path,
                {
                    "assembly_contract_version": ASSEMBLED_DAILY_CONTRACT_VERSION,
                    "campaign_id": plan.campaign_id,
                    "assembled_dataset_id": dataset_id,
                    "created_at_utc": utc_now().isoformat(),
                    "row_count": len(assembled),
                    "symbol_count": int(assembled["symbol"].nunique()),
                    "source_run_ids": state.source_run_ids,
                    "research_readiness_status_at_publication": (
                        state.research_readiness_status.value
                    ),
                    "canonical_candidate_at_publication": (
                        state.research_readiness_status
                        is CampaignAcceptanceStatus.ACCEPTED
                    ),
                    "readiness_audit_run_id": (
                        state.readiness_assessment.audit_run_id
                        if state.readiness_assessment is not None
                        else None
                    ),
                },
            )
            staging.rename(final_dir)
            output_paths = self.storage.assembled_daily_paths(plan.campaign_id, dataset_id)
            output_paths.append(final_dir / "dataset.json")
        state.assembled_dataset_id = dataset_id
        state.canonical_candidate = (
            state.research_readiness_status is CampaignAcceptanceStatus.ACCEPTED
        )
        self.write_state(state)
        return (
            dataset_id,
            assembled,
            output_paths,
            sorted(set(input_paths)),
            validation_results,
            state,
        )

    def source_rows(
        self,
        plan: DailyCampaignPlan,
        state: DailyCampaignState,
        *,
        symbols: set[str],
    ) -> tuple[pd.DataFrame, list[Path]]:
        task_lookup = {task.task_id: task for task in plan.tasks}
        cache: dict[tuple[str, str], tuple[pd.DataFrame, Path]] = {}
        frames: list[pd.DataFrame] = []
        input_paths: list[Path] = []
        for assessment in state.tasks:
            if (
                assessment.symbol not in symbols
                or assessment.status not in RESOLVED_TASK_STATUSES
                or assessment.selected_run_id is None
                or assessment.status is CampaignTaskStatus.EMPTY
            ):
                continue
            task = task_lookup[assessment.task_id]
            key = (assessment.selected_run_id, assessment.symbol)
            if key not in cache:
                paths = self._symbol_source_paths(
                    assessment.selected_run_id,
                    assessment.symbol,
                )
                if len(paths) != 1:
                    raise CampaignCompatibilityError(
                        f"Expected one normalized source path for {assessment.task_id}."
                    )
                try:
                    frame = pd.read_parquet(paths[0])
                except Exception as exc:
                    raise CampaignCompatibilityError(
                        f"Could not read normalized source for {assessment.task_id}."
                    ) from exc
                cache[key] = (frame, paths[0])
            source, path = cache[key]
            dates = pd.to_datetime(source["date"], errors="coerce").dt.date
            selected = source[
                dates.between(task.start_date, task.end_date, inclusive="both")
            ].copy()
            if selected.empty:
                raise CampaignCompatibilityError(
                    f"Completed task {task.task_id} has no source rows."
                )
            selected["source_run_id"] = assessment.selected_run_id
            selected["source_normalized_path"] = str(path)
            selected["__input_path"] = str(path)
            frames.append(selected)
            input_paths.append(path)
        if not frames:
            return pd.DataFrame(), input_paths
        combined = pd.concat(frames, ignore_index=True).sort_values(
            ["symbol", "date"], kind="stable"
        )
        return combined.reset_index(drop=True), input_paths

    def _assess_task(
        self,
        plan: DailyCampaignPlan,
        task: DailyCampaignTask,
        receipts: list[DailyCampaignReceipt],
    ) -> DailyCampaignTaskAssessment:
        if not receipts:
            return _pending_assessment(task)
        candidates = [
            self._assess_source_for_task(plan, task, receipt) for receipt in receipts
        ]
        usable = [item for item in candidates if item.status in RESOLVED_TASK_STATUSES]
        usable_run_ids = {item.selected_run_id for item in usable}
        attempt_run_ids = [receipt.source_run_id for receipt in receipts]
        if len(usable_run_ids) > 1:
            return DailyCampaignTaskAssessment(
                task_id=task.task_id,
                symbol=task.symbol,
                start_date=task.start_date,
                end_date=task.end_date,
                status=CampaignTaskStatus.INCOMPATIBLE,
                attempt_run_ids=attempt_run_ids,
                reason_codes=["multiple_compatible_successful_sources_for_task"],
            )
        if usable:
            selected = usable[-1]
            selected.attempt_run_ids = attempt_run_ids
            return selected
        priority = {
            CampaignTaskStatus.INCOMPATIBLE: 3,
            CampaignTaskStatus.STALE: 2,
            CampaignTaskStatus.FAILED: 1,
        }
        selected = max(candidates, key=lambda item: priority.get(item.status, 0))
        selected.attempt_run_ids = attempt_run_ids
        return selected

    def _assess_source_for_task(
        self,
        plan: DailyCampaignPlan,
        task: DailyCampaignTask,
        receipt: DailyCampaignReceipt,
    ) -> DailyCampaignTaskAssessment:
        def assessment(
            status: CampaignTaskStatus,
            reason_codes: list[str],
            *,
            observation_count: int = 0,
            first_observation_date: date | None = None,
            last_observation_date: date | None = None,
        ) -> DailyCampaignTaskAssessment:
            return DailyCampaignTaskAssessment(
                task_id=task.task_id,
                symbol=task.symbol,
                start_date=task.start_date,
                end_date=task.end_date,
                selected_run_id=receipt.source_run_id,
                attempt_run_ids=[receipt.source_run_id],
                status=status,
                observation_count=observation_count,
                first_observation_date=first_observation_date,
                last_observation_date=last_observation_date,
                reason_codes=reason_codes,
            )

        try:
            manifest = self.storage.read_manifest(receipt.source_run_id)
        except (OSError, ValueError):
            return assessment(
                CampaignTaskStatus.INCOMPATIBLE,
                ["source_manifest_invalid"],
            )
        if manifest is None:
            return assessment(
                CampaignTaskStatus.INCOMPATIBLE,
                ["source_manifest_missing"],
            )
        if manifest.command != "data backfill-daily":
            return assessment(
                CampaignTaskStatus.INCOMPATIBLE,
                ["source_command_incompatible"],
            )
        if manifest.status != "success":
            return assessment(
                CampaignTaskStatus.FAILED,
                ["source_run_failed"],
            )
        reasons = self._manifest_compatibility_reasons(plan, task, manifest)
        if reasons:
            return assessment(CampaignTaskStatus.INCOMPATIBLE, reasons)
        paths = self._symbol_source_paths(receipt.source_run_id, task.symbol)
        if not paths:
            if (
                manifest.row_counts.get("normalized", -1) == 0
                and manifest.row_counts.get("chunks_empty", 0) >= 1
                and manifest.parameters.get("allow_empty_success") is True
                and manifest.data_contract_versions.get("normalized_daily")
                == plan.normalized_daily_contract_version
                and manifest.parameters.get("price_adjustment_semantics")
                == plan.price_adjustment_semantics
                and manifest.parameters.get("expected_adjusted_flag")
                == plan.expected_adjusted_flag
            ):
                return assessment(
                    CampaignTaskStatus.EMPTY,
                    ["provider_returned_empty_chunk"],
                )
            return assessment(
                CampaignTaskStatus.INCOMPATIBLE,
                ["normalized_symbol_source_missing"],
            )
        if len(paths) != 1:
            return assessment(
                CampaignTaskStatus.INCOMPATIBLE,
                ["multiple_normalized_paths_for_source_symbol"],
            )
        try:
            source = pd.read_parquet(paths[0])
        except Exception:
            return assessment(
                CampaignTaskStatus.INCOMPATIBLE,
                ["normalized_daily_source_unreadable"],
            )
        required_partition_columns = {"symbol", "exchange", "date", "adjusted_flag"}
        if not required_partition_columns <= set(map(str, source.columns)):
            return assessment(
                CampaignTaskStatus.INCOMPATIBLE,
                ["normalized_daily_required_columns_missing"],
            )
        source_validation = validate_daily_ohlcv(source)
        if has_blocking_errors(source_validation):
            return assessment(
                CampaignTaskStatus.INCOMPATIBLE,
                ["normalized_daily_validation_failed"],
            )
        source_dates = pd.to_datetime(source["date"], errors="coerce").dt.date
        invalid_structure_reasons: list[str] = []
        if source_dates.isna().any():
            invalid_structure_reasons.append("source_contains_invalid_dates")
        if source["symbol"].isna().any():
            invalid_structure_reasons.append("source_contains_null_symbols")
        if source["exchange"].isna().any():
            invalid_structure_reasons.append("source_contains_null_exchanges")
        if set(source["symbol"].dropna().astype(str).str.strip().str.upper()) != {
            task.symbol
        }:
            invalid_structure_reasons.append("source_symbol_partition_mismatch")
        if set(source["exchange"].dropna().astype(str).str.strip().str.upper()) != {
            plan.exchange
        }:
            invalid_structure_reasons.append("source_exchange_rows_mismatch")
        if invalid_structure_reasons:
            return assessment(CampaignTaskStatus.INCOMPATIBLE, invalid_structure_reasons)
        manifest_start = date.fromisoformat(str(manifest.start_date))
        manifest_end = date.fromisoformat(str(manifest.end_date))
        outside_manifest = source_dates.notna() & ~source_dates.between(
            manifest_start,
            manifest_end,
            inclusive="both",
        )
        if outside_manifest.any():
            return assessment(
                CampaignTaskStatus.INCOMPATIBLE,
                ["source_rows_outside_manifest_range"],
            )
        task_rows = source[
            source_dates.between(task.start_date, task.end_date, inclusive="both")
        ].copy()
        if task_rows.empty:
            return assessment(
                CampaignTaskStatus.EMPTY,
                ["successful_source_contains_no_rows_for_chunk"],
            )
        task_dates = pd.to_datetime(task_rows["date"], errors="coerce")
        if (task_dates.dt.dayofweek >= 5).any():
            return assessment(
                CampaignTaskStatus.INCOMPATIBLE,
                ["weekend_observations_in_daily_source"],
            )
        if task_rows[["open", "high", "low", "close", "volume"]].isna().any(axis=None):
            return assessment(
                CampaignTaskStatus.INCOMPATIBLE,
                ["missing_ohlcv_values_in_daily_source"],
            )
        policy = resolve_daily_unit_policy(task_rows)
        if not policy.can_compute_vnd or policy.source_provenance != plan.expected_unit_provenance:
            return assessment(
                CampaignTaskStatus.INCOMPATIBLE,
                ["row_unit_provenance_incompatible"],
            )
        adjusted = task_rows["adjusted_flag"]
        if plan.expected_adjusted_flag is None:
            adjustment_incompatible = adjusted.notna().any()
        else:
            adjustment_incompatible = (
                adjusted.isna().any()
                or (adjusted.astype(bool) != plan.expected_adjusted_flag).any()
            )
        if adjustment_incompatible:
            return assessment(
                CampaignTaskStatus.INCOMPATIBLE,
                ["row_adjustment_semantics_incompatible"],
            )
        validation = validate_daily_ohlcv(task_rows)
        if has_blocking_errors(validation):
            return assessment(
                CampaignTaskStatus.INCOMPATIBLE,
                ["normalized_daily_validation_failed"],
            )
        first_date = pd.Timestamp(task_rows["date"].min()).date()
        last_date = pd.Timestamp(task_rows["date"].max()).date()
        stale_days = max((task.end_date - last_date).days, 0)
        if stale_days > plan.stale_after_calendar_days:
            return assessment(
                CampaignTaskStatus.STALE,
                ["last_observation_stale_for_chunk_end"],
                observation_count=len(task_rows),
                first_observation_date=first_date,
                last_observation_date=last_date,
            )
        reason_codes = ["compatible_successful_source"]
        if "normalized_daily" not in manifest.data_contract_versions:
            reason_codes.append("legacy_contract_structurally_validated")
        return assessment(
            CampaignTaskStatus.COMPLETE,
            reason_codes,
            observation_count=len(task_rows),
            first_observation_date=first_date,
            last_observation_date=last_date,
        )

    def _manifest_compatibility_reasons(
        self,
        plan: DailyCampaignPlan,
        task: DailyCampaignTask,
        manifest: DatasetManifest,
    ) -> list[str]:
        reasons: list[str] = []
        if manifest.provider != plan.provider:
            reasons.append("provider_mismatch")
        if manifest.exchange != plan.exchange:
            reasons.append("exchange_mismatch")
        if manifest.resolution is not None and manifest.resolution != plan.source_resolution:
            reasons.append("source_resolution_mismatch")
        if task.symbol not in manifest.symbols:
            reasons.append("symbol_not_declared_by_source_manifest")
        try:
            source_start = date.fromisoformat(str(manifest.start_date))
            source_end = date.fromisoformat(str(manifest.end_date))
        except ValueError:
            reasons.append("manifest_date_range_invalid")
            return reasons
        if source_start > task.start_date or source_end < task.end_date:
            reasons.append("source_range_does_not_cover_task")
        declared_contract = manifest.data_contract_versions.get("normalized_daily")
        if (
            declared_contract is not None
            and declared_contract != plan.normalized_daily_contract_version
        ):
            reasons.append("normalized_daily_contract_mismatch")
        declared_adjustment = manifest.parameters.get("price_adjustment_semantics")
        if (
            declared_adjustment is not None
            and declared_adjustment != plan.price_adjustment_semantics
        ):
            reasons.append("price_adjustment_semantics_mismatch")
        policy = manifest.unit_provenance
        declared_empty_provenance = manifest.parameters.get(
            "declared_source_unit_provenance"
        )
        if (
            policy is not None
            and policy.source_provenance == plan.expected_unit_provenance
        ) or declared_empty_provenance == plan.expected_unit_provenance.model_dump(mode="json"):
            pass
        else:
            reasons.append("manifest_unit_provenance_incompatible")
        return reasons

    def _symbol_source_paths(self, run_id: str, symbol: str) -> list[Path]:
        return [
            path
            for path in self.storage.normalized_dataset_paths("daily", run_id=run_id)
            if path.parent.name == f"symbol={symbol.upper()}"
        ]

    @staticmethod
    def _symbol_statuses(
        plan: DailyCampaignPlan,
        assessments: list[DailyCampaignTaskAssessment],
    ) -> dict[str, str]:
        by_symbol: dict[str, list[CampaignTaskStatus]] = {symbol: [] for symbol in plan.symbols}
        for assessment in assessments:
            by_symbol[assessment.symbol].append(assessment.status)
        statuses: dict[str, str] = {}
        for symbol, task_statuses in by_symbol.items():
            if all(status in RESOLVED_TASK_STATUSES for status in task_statuses):
                statuses[symbol] = "complete"
            elif CampaignTaskStatus.INCOMPATIBLE in task_statuses:
                statuses[symbol] = "incompatible"
            elif CampaignTaskStatus.STALE in task_statuses:
                statuses[symbol] = "stale"
            elif CampaignTaskStatus.FAILED in task_statuses:
                statuses[symbol] = "failed"
            elif any(status in RESOLVED_TASK_STATUSES for status in task_statuses):
                statuses[symbol] = "partial"
            else:
                statuses[symbol] = "pending"
        return statuses

    def _complete_symbols(
        self,
        plan: DailyCampaignPlan,
        assessments: list[DailyCampaignTaskAssessment],
    ) -> set[str]:
        statuses = self._symbol_statuses(plan, assessments)
        return {symbol for symbol, status in statuses.items() if status == "complete"}


def campaign_source_evidence_digest(
    plan: DailyCampaignPlan,
    state: DailyCampaignState,
) -> str:
    task_evidence = [
        {
            "task_id": task.task_id,
            "status": task.status.value,
            "selected_run_id": task.selected_run_id,
            "observation_count": task.observation_count,
            "first_observation_date": (
                task.first_observation_date.isoformat()
                if task.first_observation_date is not None
                else None
            ),
            "last_observation_date": (
                task.last_observation_date.isoformat()
                if task.last_observation_date is not None
                else None
            ),
            "reason_codes": task.reason_codes,
        }
        for task in state.tasks
    ]
    payload = {
        "campaign_id": plan.campaign_id,
        "campaign_contract_version": plan.campaign_contract_version,
        "normalized_daily_contract_version": plan.normalized_daily_contract_version,
        "task_evidence": task_evidence,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def evaluate_campaign_readiness(
    plan: DailyCampaignPlan,
    state: DailyCampaignState,
    coverage: pd.DataFrame,
    *,
    audit_run_id: str,
    coverage_config: DailyCoverageConfig,
    readiness_policy: DailyCampaignReadinessPolicy,
    source_row_count: int,
    duplicate_symbol_date_count: int,
    task_range_gap_count: int,
    task_range_overlap_count: int,
    vnd_traded_value_permitted: bool,
) -> DailyCampaignReadinessAssessment:
    if readiness_policy.policy_version != DAILY_CAMPAIGN_READINESS_POLICY_VERSION:
        raise ValueError(
            f"Unsupported campaign readiness policy: {readiness_policy.policy_version}."
        )

    universe_symbol_count = len(plan.symbols)
    coverage_symbols = set(coverage["symbol"].dropna().astype(str))
    status_counts = {
        status.value: int((coverage["coverage_status"] == status.value).sum())
        for status in DailyCoverageStatus
    }
    vnd_usable_count = status_counts[DailyCoverageStatus.USABLE_VND.value]
    absent_count = status_counts[DailyCoverageStatus.ABSENT.value]
    rejected_statuses = {
        DailyCoverageStatus.NOT_INGESTED,
        DailyCoverageStatus.BLOCKING_QUALITY_ISSUES,
        DailyCoverageStatus.STALE,
        DailyCoverageStatus.INSUFFICIENT_HISTORY,
        DailyCoverageStatus.SPARSE,
        DailyCoverageStatus.USABLE_NON_MONETARY,
    }
    rejected_coverage_count = sum(
        status_counts[status.value] for status in rejected_statuses
    )
    vnd_usable_ratio = (
        vnd_usable_count / universe_symbol_count if universe_symbol_count else 0.0
    )
    absent_ratio = absent_count / universe_symbol_count if universe_symbol_count else 0.0
    coverage_summary = summarize_daily_coverage(coverage)
    common_overlap = bool(coverage_summary["common_vnd_overlap_available"])
    source_rows_present = source_row_count > 0
    source_symbol_dates_unique = duplicate_symbol_date_count == 0
    task_ranges_valid = task_range_gap_count == 0 and task_range_overlap_count == 0
    structural_assembly_compatible = (
        state.assembly_compatible
        and source_rows_present
        and source_symbol_dates_unique
        and task_ranges_valid
    )
    criteria = {
        "campaign_complete": state.campaign_complete,
        "task_sources_assembly_compatible": state.assembly_compatible,
        "source_rows_present": source_rows_present,
        "source_symbol_dates_unique": source_symbol_dates_unique,
        "task_ranges_contiguous_non_overlapping": task_ranges_valid,
        "structural_assembly_compatible": structural_assembly_compatible,
        "full_universe_coverage_audited": (
            len(coverage) == universe_symbol_count
            and coverage_symbols == set(plan.symbols)
        ),
        "no_rejected_coverage_statuses": rejected_coverage_count == 0,
        "minimum_vnd_usable_symbol_ratio": (
            vnd_usable_ratio >= readiness_policy.min_vnd_usable_symbol_ratio
        ),
        "maximum_absent_symbol_ratio": (
            absent_ratio <= readiness_policy.max_absent_symbol_ratio
        ),
        "common_vnd_date_overlap": (
            common_overlap or not readiness_policy.require_common_vnd_date_overlap
        ),
        "vnd_unit_provenance_permitted": vnd_traded_value_permitted,
    }
    coverage_criteria = [
        "full_universe_coverage_audited",
        "source_symbol_dates_unique",
        "no_rejected_coverage_statuses",
        "minimum_vnd_usable_symbol_ratio",
        "maximum_absent_symbol_ratio",
        "common_vnd_date_overlap",
        "vnd_unit_provenance_permitted",
    ]
    coverage_accepted = all(criteria[name] for name in coverage_criteria)
    research_ready = (
        criteria["campaign_complete"]
        and criteria["structural_assembly_compatible"]
        and coverage_accepted
    )
    reason_by_criterion = {
        "campaign_complete": "campaign_incomplete",
        "task_sources_assembly_compatible": "task_sources_assembly_incompatible",
        "source_rows_present": "campaign_source_rows_missing",
        "source_symbol_dates_unique": "duplicate_symbol_date_rows_present",
        "task_ranges_contiguous_non_overlapping": "task_range_gap_or_overlap_present",
        "structural_assembly_compatible": "structural_assembly_incompatible",
        "full_universe_coverage_audited": "full_universe_coverage_not_audited",
        "no_rejected_coverage_statuses": "rejected_coverage_statuses_present",
        "minimum_vnd_usable_symbol_ratio": "vnd_usable_symbol_ratio_below_minimum",
        "maximum_absent_symbol_ratio": "absent_symbol_ratio_above_maximum",
        "common_vnd_date_overlap": "common_vnd_date_overlap_missing",
        "vnd_unit_provenance_permitted": "vnd_unit_provenance_not_permitted",
    }
    reason_codes = [
        reason_by_criterion[name] for name, passed in criteria.items() if not passed
    ]
    metrics: dict[str, int | float | bool] = {
        "universe_symbol_count": universe_symbol_count,
        "audited_symbol_count": len(coverage),
        "source_row_count": source_row_count,
        "duplicate_symbol_date_count": duplicate_symbol_date_count,
        "task_range_gap_count": task_range_gap_count,
        "task_range_overlap_count": task_range_overlap_count,
        "vnd_usable_symbol_count": vnd_usable_count,
        "vnd_usable_symbol_ratio": vnd_usable_ratio,
        "absent_symbol_count": absent_count,
        "absent_symbol_ratio": absent_ratio,
        "rejected_coverage_symbol_count": rejected_coverage_count,
        "common_vnd_date_overlap_available": common_overlap,
    }
    return DailyCampaignReadinessAssessment(
        readiness_contract_version=DAILY_CAMPAIGN_READINESS_CONTRACT_VERSION,
        audit_run_id=audit_run_id,
        source_evidence_digest=campaign_source_evidence_digest(plan, state),
        coverage_config=coverage_config,
        policy=readiness_policy,
        coverage_quality_status=(
            CampaignAcceptanceStatus.ACCEPTED
            if coverage_accepted
            else CampaignAcceptanceStatus.REJECTED
        ),
        research_readiness_status=(
            CampaignAcceptanceStatus.ACCEPTED
            if research_ready
            else CampaignAcceptanceStatus.REJECTED
        ),
        criteria=criteria,
        metrics=metrics,
        reason_codes=reason_codes,
        known_risks=CAMPAIGN_KNOWN_RISKS,
    )


def assembled_dataset_id(plan: DailyCampaignPlan, state: DailyCampaignState) -> str:
    mapping = [
        {
            "task_id": task.task_id,
            "status": task.status.value,
            "selected_run_id": task.selected_run_id,
        }
        for task in state.tasks
    ]
    payload = {
        "assembly_contract_version": ASSEMBLED_DAILY_CONTRACT_VERSION,
        "campaign_plan": plan.model_dump(mode="json"),
        "task_sources": mapping,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"assembled-daily-v1-{digest}"


def write_campaign_audit_report(
    plan: DailyCampaignPlan,
    state: DailyCampaignState,
    coverage: pd.DataFrame,
    *,
    summary: dict[str, Any],
    json_path: Path,
    markdown_path: Path,
) -> tuple[Path, Path]:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "audit_contract_version": DAILY_CAMPAIGN_AUDIT_CONTRACT_VERSION,
        "plan": plan.model_dump(mode="json"),
        "summary": summary,
        "readiness_assessment": (
            state.readiness_assessment.model_dump(mode="json")
            if state.readiness_assessment is not None
            else None
        ),
        "tasks": [item.model_dump(mode="json") for item in state.tasks],
        "coverage": json.loads(coverage.to_json(orient="records", date_format="iso")),
    }
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    symbol_statuses = DailyCampaignManager._symbol_statuses(plan, state.tasks)
    lines = [
        "# Daily Ingestion Campaign Audit",
        "",
        f"Campaign: `{plan.campaign_id}`",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Universe symbols | {len(plan.symbols)} |",
        f"| Chunk tasks | {len(plan.tasks)} |",
        f"| Complete symbols | {summary['complete_symbol_count']} |",
        f"| Virtual source rows | {summary['virtual_source_row_count']} |",
        f"| Duplicate symbol/date rows | {summary['duplicate_symbol_date_count']} |",
        f"| Campaign complete | {summary['campaign_complete']} |",
        f"| Assembly compatible | {summary['assembly_compatible']} |",
        f"| Assembly ready | {summary['assembly_ready']} |",
        f"| Coverage-quality status | {summary['coverage_quality_status']} |",
        f"| Research-readiness status | {summary['research_readiness_status']} |",
        f"| Canonical candidate | {summary['canonical_candidate']} |",
        "",
        "## Task Status",
        "",
        "| Status | Tasks |",
        "| --- | ---: |",
    ]
    for status, count in sorted(state.task_counts.items()):
        lines.append(f"| {status} | {count} |")
    lines.extend(
        [
            "",
            "## Symbol Status",
            "",
            "| Symbol | Status |",
            "| --- | --- |",
        ]
    )
    for symbol, status in sorted(symbol_statuses.items()):
        lines.append(f"| {symbol} | {status} |")
    readiness = state.readiness_assessment
    if readiness is not None:
        lines.extend(
            [
                "",
                "## Research Readiness Policy",
                "",
                f"- Contract: `{readiness.readiness_contract_version}`",
                f"- Policy: `{readiness.policy.policy_version}`",
                f"- Scope: `{readiness.policy.research_scope}`",
                "- Minimum VND-usable symbol ratio: "
                f"`{readiness.policy.min_vnd_usable_symbol_ratio}`",
                "- Maximum absent symbol ratio: "
                f"`{readiness.policy.max_absent_symbol_ratio}`",
                "- Common VND date overlap required: "
                f"`{readiness.policy.require_common_vnd_date_overlap}`",
                "",
                "## Readiness Criteria",
                "",
                "| Criterion | Passed |",
                "| --- | --- |",
            ]
        )
        for criterion, passed in readiness.criteria.items():
            lines.append(f"| {criterion} | {passed} |")
        lines.extend(
            [
                "",
                "Rejection reasons: "
                + (
                    ", ".join(f"`{reason}`" for reason in readiness.reason_codes)
                    if readiness.reason_codes
                    else "None."
                ),
            ]
        )
    lines.extend(
        [
            "",
            "## Known Risks",
            "",
            *[f"- `{risk}`" for risk in CAMPAIGN_KNOWN_RISKS],
        ]
    )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def _pending_assessment(task: DailyCampaignTask) -> DailyCampaignTaskAssessment:
    return DailyCampaignTaskAssessment(
        task_id=task.task_id,
        symbol=task.symbol,
        start_date=task.start_date,
        end_date=task.end_date,
        status=CampaignTaskStatus.PENDING,
        reason_codes=["no_campaign_receipt"],
    )


def _write_model_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
