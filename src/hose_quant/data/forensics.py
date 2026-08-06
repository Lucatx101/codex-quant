from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from hose_quant.data.contracts import DAILY_CAMPAIGN_FORENSIC_AUDIT_CONTRACT_VERSION
from hose_quant.data.models import (
    CampaignTaskStatus,
    DailyCampaignPlan,
    DailyCampaignState,
    DailyCampaignTaskAssessment,
    DatasetManifest,
)
from hose_quant.data.normalizers import normalize_daily_ohlcv
from hose_quant.data.storage import DataStorage
from hose_quant.data.validators import validate_daily_ohlcv

PRICE_COLUMNS = ("open", "high", "low", "close")
VALUE_COLUMNS = (*PRICE_COLUMNS, "volume")

OPEN_PREVIOUS_CLOSE_CATEGORY = "kbs_open_matches_previous_close_outside_range"
CLOSE_RANGE_CATEGORY = "kbs_close_outside_reported_range_semantics_unresolved"
MIXED_RANGE_CATEGORY = "mixed_kbs_open_and_close_range_inconsistency"
GENERIC_RANGE_CATEGORY = "kbs_ohlc_range_inconsistency_unresolved"
NO_REPRODUCTION_CATEGORY = "ohlc_violation_not_reproduced_from_local_raw"
MISSING_EVIDENCE_CATEGORY = "required_local_evidence_missing"
HISTORICAL_STALE_CATEGORY = "historical_missing_tail_with_later_resumption"
CURRENT_STALE_CATEGORY = "campaign_end_missing_tail_without_later_campaign_observation"
UNRESOLVED_STALE_CATEGORY = "historical_missing_tail_without_later_observation"
NORMALIZATION_MISMATCH_CATEGORY = "stored_normalization_mismatch_requires_investigation"
POSSIBLE_TRUNCATION_CATEGORY = "provider_response_at_documented_row_cap"


@dataclass(frozen=True)
class ForensicAuditArtifacts:
    payload: dict[str, Any]
    input_paths: list[Path]
    json_path: Path
    markdown_path: Path


@dataclass
class _SourceEvidence:
    manifest_path: Path
    manifest: DatasetManifest | None
    raw_path: Path | None
    raw: pd.DataFrame | None
    normalized_path: Path | None
    load_errors: list[str]


@dataclass(frozen=True)
class _NextObservation:
    observation_date: date
    source_run_id: str
    source: _SourceEvidence


class _EvidenceReader:
    def __init__(self, storage: DataStorage) -> None:
        self.storage = storage
        self.cache: dict[str, _SourceEvidence] = {}
        self.input_paths: set[Path] = set()

    def source(self, run_id: str) -> _SourceEvidence:
        if run_id in self.cache:
            return self.cache[run_id]

        manifest_path = self.storage.manifest_path(run_id)
        self.input_paths.add(manifest_path)
        errors: list[str] = []
        manifest: DatasetManifest | None = None
        try:
            manifest = self.storage.read_manifest(run_id)
        except (OSError, ValueError) as exc:
            errors.append(f"manifest_unreadable:{type(exc).__name__}")
        if manifest is None and not errors:
            errors.append("manifest_missing")

        raw_path = self._raw_path(run_id, manifest)
        raw: pd.DataFrame | None = None
        if raw_path is None:
            errors.append("raw_jsonl_missing")
        else:
            self.input_paths.add(raw_path)
            try:
                raw = pd.read_json(raw_path, lines=True)
            except (OSError, ValueError) as exc:
                errors.append(f"raw_jsonl_unreadable:{type(exc).__name__}")

        normalized_path = self._normalized_path(run_id, manifest)
        if normalized_path is not None:
            self.input_paths.add(normalized_path)

        evidence = _SourceEvidence(
            manifest_path=manifest_path,
            manifest=manifest,
            raw_path=raw_path,
            raw=raw,
            normalized_path=normalized_path,
            load_errors=errors,
        )
        self.cache[run_id] = evidence
        return evidence

    def _raw_path(self, run_id: str, manifest: DatasetManifest | None) -> Path | None:
        candidates: list[Path] = []
        if manifest is not None:
            candidates.extend(
                Path(value) for value in manifest.output_paths if Path(value).name == "raw.jsonl"
            )
        candidates.append(self.storage.raw_dataset_dir("daily", run_id) / "raw.jsonl")
        return next((path for path in candidates if path.exists()), None)

    def _normalized_path(
        self,
        run_id: str,
        manifest: DatasetManifest | None,
    ) -> Path | None:
        candidates: list[Path] = []
        if manifest is not None:
            candidates.extend(
                Path(value)
                for value in manifest.output_paths
                if Path(value).suffix == ".parquet"
                and "normalized" in Path(value).parts
                and Path(value).stem == run_id
            )
        candidates.extend(self.storage.normalized_dataset_paths("daily", run_id=run_id))
        return next((path for path in candidates if path.exists()), None)


def classify_ohlc_relationship_evidence(
    frame: pd.DataFrame,
    *,
    previous_close_by_date: dict[date, float] | None = None,
) -> dict[str, Any]:
    """Classify standard OHLC range violations without changing any value."""
    required = {"date", *VALUE_COLUMNS}
    missing = sorted(required - set(map(str, frame.columns)))
    if missing:
        return {
            "category": MISSING_EVIDENCE_CATEGORY,
            "missing_columns": missing,
            "affected_rows": [],
            "relation_counts": {},
            "open_violation_row_count": 0,
            "close_violation_row_count": 0,
            "open_matches_previous_close_count": 0,
        }

    previous_close_by_date = previous_close_by_date or {}
    relation_counts: Counter[str] = Counter()
    affected_rows: list[dict[str, Any]] = []
    open_violation_count = 0
    close_violation_count = 0
    open_matches_previous_close_count = 0

    ordered = frame.sort_values("date", kind="stable").reset_index(drop=True)
    for row in ordered.itertuples(index=False):
        row_date = _as_date(row.date)
        values = {column: _as_float(getattr(row, column)) for column in VALUE_COLUMNS}
        relations = _violated_relations(values)
        if not relations:
            continue
        relation_counts.update(relations)
        open_violation = any(relation.endswith("_open") for relation in relations)
        close_violation = any(relation.endswith("_close") for relation in relations)
        open_violation_count += int(open_violation)
        close_violation_count += int(close_violation)
        previous_close = previous_close_by_date.get(row_date)
        open_matches_previous = bool(
            open_violation
            and previous_close is not None
            and values["open"] == previous_close
        )
        open_matches_previous_close_count += int(open_matches_previous)
        affected_rows.append(
            {
                "date": row_date.isoformat(),
                "relations": relations,
                "normalized": values,
                "previous_observation_close": previous_close,
                "open_matches_previous_observation_close": open_matches_previous,
            }
        )

    has_open = open_violation_count > 0
    has_close = close_violation_count > 0
    every_open_matches_previous = bool(
        has_open and open_matches_previous_close_count == open_violation_count
    )
    if not affected_rows:
        category = NO_REPRODUCTION_CATEGORY
    elif has_open and not has_close and every_open_matches_previous:
        category = OPEN_PREVIOUS_CLOSE_CATEGORY
    elif has_close and not has_open:
        category = CLOSE_RANGE_CATEGORY
    elif has_open and has_close and every_open_matches_previous:
        category = MIXED_RANGE_CATEGORY
    else:
        category = GENERIC_RANGE_CATEGORY
    return {
        "category": category,
        "affected_rows": affected_rows,
        "relation_counts": dict(sorted(relation_counts.items())),
        "open_violation_row_count": open_violation_count,
        "close_violation_row_count": close_violation_count,
        "open_matches_previous_close_count": open_matches_previous_close_count,
    }


def classify_stale_edge(
    *,
    task_end: date,
    campaign_end: date,
    last_observation_date: date,
    next_observation_date: date | None,
    response_row_count: int,
    normalized_matches_raw: bool,
) -> str:
    """Classify a stale edge while leaving unknown exchange-status causes explicit."""
    if not normalized_matches_raw:
        return NORMALIZATION_MISMATCH_CATEGORY
    if response_row_count >= 1000:
        return POSSIBLE_TRUNCATION_CATEGORY
    if task_end == campaign_end:
        return CURRENT_STALE_CATEGORY
    if next_observation_date is not None and next_observation_date > task_end:
        return HISTORICAL_STALE_CATEGORY
    return UNRESOLVED_STALE_CATEGORY


def write_daily_campaign_forensic_audit(
    plan: DailyCampaignPlan,
    state: DailyCampaignState,
    *,
    storage: DataStorage,
    generated_at_utc: datetime,
    json_path: Path,
    markdown_path: Path,
) -> ForensicAuditArtifacts:
    if plan.campaign_id != state.campaign_id:
        raise ValueError("Campaign plan and state IDs do not match.")

    reader = _EvidenceReader(storage)
    plan_path = storage.daily_campaign_plan_path(plan.campaign_id)
    state_path = storage.daily_campaign_state_path(plan.campaign_id)
    reader.input_paths.update({plan_path, state_path})
    unresolved = [
        task
        for task in state.tasks
        if task.status in {CampaignTaskStatus.FAILED, CampaignTaskStatus.STALE}
    ]
    records = [
        _classify_task(plan, state, task, reader)
        for task in unresolved
    ]
    summary = _summarize(plan, state, records)
    payload = {
        "forensic_audit_contract_version": (
            DAILY_CAMPAIGN_FORENSIC_AUDIT_CONTRACT_VERSION
        ),
        "campaign_id": plan.campaign_id,
        "generated_at_utc": generated_at_utc.isoformat(),
        "source_evidence": {
            "plan_path": str(plan_path),
            "plan_sha256": _sha256_or_none(plan_path),
            "state_path": str(state_path),
            "state_sha256": _sha256_or_none(state_path),
        },
        "method": {
            "provider_calls": 0,
            "raw_evidence_immutable": True,
            "failed_normalization_mode": "reconstructed_in_memory_without_publication",
            "stale_normalization_mode": "raw_jsonl_compared_to_stored_parquet",
            "ohlc_contract": "high >= low; high >= open/close; low <= open/close",
            "price_adjustment_semantics": plan.price_adjustment_semantics,
            "unknowns_remain_explicit": True,
        },
        "summary": summary,
        "tasks": records,
    }
    _write_json_atomic(json_path, payload)
    _write_text_atomic(markdown_path, _render_markdown(payload))
    return ForensicAuditArtifacts(
        payload=payload,
        input_paths=sorted(reader.input_paths),
        json_path=json_path,
        markdown_path=markdown_path,
    )


def _classify_task(
    plan: DailyCampaignPlan,
    state: DailyCampaignState,
    task: DailyCampaignTaskAssessment,
    reader: _EvidenceReader,
) -> dict[str, Any]:
    if task.selected_run_id is None:
        return _missing_task_record(task, ["selected_run_id_missing"])
    source = reader.source(task.selected_run_id)
    if source.raw is None or source.manifest is None:
        return _missing_task_record(task, source.load_errors)
    if task.status is CampaignTaskStatus.FAILED:
        return _classify_failed_task(plan, task, source)
    return _classify_stale_task(plan, state, task, source, reader)


def _classify_failed_task(
    plan: DailyCampaignPlan,
    task: DailyCampaignTaskAssessment,
    source: _SourceEvidence,
) -> dict[str, Any]:
    assert source.raw is not None
    assert source.manifest is not None
    normalized = normalize_daily_ohlcv(
        source.raw,
        symbol=task.symbol,
        exchange=plan.exchange,
        ingestion_timestamp_utc=source.manifest.finished_at_utc,
        unit_provenance=plan.expected_unit_provenance,
    )
    previous_close = _previous_close_by_date(normalized)
    relationship = classify_ohlc_relationship_evidence(
        normalized,
        previous_close_by_date=previous_close,
    )
    raw_by_date = _raw_values_by_date(source.raw)
    for row in relationship["affected_rows"]:
        row["raw"] = raw_by_date.get(row["date"])
    comparison = _compare_raw_and_normalized(source.raw, normalized)
    validation = [
        {
            "check_name": result.check_name,
            "affected_row_count": result.affected_row_count,
            "blocks_output": result.blocks_output,
        }
        for result in validate_daily_ohlcv(normalized)
        if result.blocks_output
    ]
    category = str(relationship["category"])
    decision = _failed_decision(category)
    evidence_complete = bool(
        relationship["affected_rows"]
        and comparison["exact_numeric_and_date_match"]
        and not source.load_errors
    )
    return {
        **_task_identity(task),
        "root_cause_category": category,
        "root_cause_scope": "captured_vnstock_kbs_wrapper_output",
        "supporting_evidence": {
            **_source_artifacts(source),
            "manifest_status": source.manifest.status,
            "manifest_error_summary": source.manifest.error_summary,
            "provider_call_count": source.manifest.provider_call_count,
            "raw_row_count": len(source.raw),
            "normalization_reconstructed_in_memory": True,
            "raw_normalized_comparison": comparison,
            "blocking_validator_results": validation,
            **relationship,
            "affected_dates": [
                row["date"] for row in relationship["affected_rows"]
            ],
        },
        "determinism": {
            "stored_evidence_recalculates_identically": evidence_complete,
            "all_task_rows_live_refetched": False,
            "interpretation": (
                "Deterministic for the immutable captured output; representative live "
                "refetch evidence is assessed separately."
            ),
        },
        "retry_justified_now": False,
        "code_fix_required": decision["code_fix_required"],
        "disposition": decision["disposition"],
        "next_action": decision["next_action"],
        "evidence_complete": evidence_complete,
    }


def _classify_stale_task(
    plan: DailyCampaignPlan,
    state: DailyCampaignState,
    task: DailyCampaignTaskAssessment,
    source: _SourceEvidence,
    reader: _EvidenceReader,
) -> dict[str, Any]:
    assert source.raw is not None
    assert source.manifest is not None
    stored: pd.DataFrame | None = None
    errors = list(source.load_errors)
    if source.normalized_path is None:
        errors.append("normalized_parquet_missing")
    else:
        try:
            stored = pd.read_parquet(source.normalized_path)
        except (OSError, ValueError):
            errors.append("normalized_parquet_unreadable")
    if stored is None or stored.empty:
        return _missing_task_record(task, errors)

    comparison = _compare_raw_and_normalized(source.raw, stored)
    observed_dates = sorted(_frame_dates(stored))
    if not observed_dates:
        return _missing_task_record(task, [*errors, "normalized_dates_missing"])
    first_date = observed_dates[0]
    last_date = observed_dates[-1]
    next_observation = _next_campaign_observation(task, state, reader, last_date)
    next_date = (
        next_observation.observation_date if next_observation is not None else None
    )
    category = classify_stale_edge(
        task_end=task.end_date,
        campaign_end=plan.end_date,
        last_observation_date=last_date,
        next_observation_date=next_date,
        response_row_count=len(source.raw),
        normalized_matches_raw=bool(comparison["exact_numeric_and_date_match"]),
    )
    decision = _stale_decision(category)
    evidence_complete = bool(
        comparison["exact_numeric_and_date_match"]
        and not errors
        and source.manifest.status == "success"
    )
    return {
        **_task_identity(task),
        "root_cause_category": category,
        "root_cause_scope": "missing_source_observations_with_event_cause_unresolved",
        "supporting_evidence": {
            **_source_artifacts(source),
            "manifest_status": source.manifest.status,
            "provider_call_count": source.manifest.provider_call_count,
            "raw_row_count": len(source.raw),
            "stored_normalized_row_count": len(stored),
            "raw_normalized_comparison": comparison,
            "first_observation_date": first_date.isoformat(),
            "last_observation_date": last_date.isoformat(),
            "requested_end_date": task.end_date.isoformat(),
            "missing_tail_calendar_days": (task.end_date - last_date).days,
            "next_campaign_observation_date": (
                next_date.isoformat() if next_date is not None else None
            ),
            "next_campaign_observation_evidence": (
                {
                    "source_run_id": next_observation.source_run_id,
                    "manifest_path": str(next_observation.source.manifest_path),
                    "manifest_sha256": _sha256_or_none(
                        next_observation.source.manifest_path
                    ),
                    "raw_path": (
                        str(next_observation.source.raw_path)
                        if next_observation.source.raw_path is not None
                        else None
                    ),
                    "raw_sha256": _sha256_or_none(next_observation.source.raw_path),
                }
                if next_observation is not None
                else None
            ),
            "resumption_gap_calendar_days": (
                (next_date - last_date).days if next_date is not None else None
            ),
            "response_below_1000_row_cap": len(source.raw) < 1000,
            "request_bounds_match_task": (
                source.manifest.start_date == task.start_date.isoformat()
                and source.manifest.end_date == task.end_date.isoformat()
            ),
            "current_snapshot_candidate": task.symbol in plan.symbols,
            "event_cause": "unresolved",
            "edge_row": _edge_evidence(source.raw, stored, last_date),
        },
        "determinism": {
            "stored_evidence_recalculates_identically": evidence_complete,
            "all_task_edges_live_refetched": False,
            "interpretation": (
                "The missing edge is deterministic in local evidence; exact listing, halt, "
                "suspension, or sparse-trading cause is not established."
            ),
        },
        "retry_justified_now": False,
        "conditional_retry_trigger": decision["conditional_retry_trigger"],
        "code_fix_required": category == NORMALIZATION_MISMATCH_CATEGORY,
        "disposition": decision["disposition"],
        "next_action": decision["next_action"],
        "evidence_complete": evidence_complete,
    }


def _missing_task_record(
    task: DailyCampaignTaskAssessment,
    errors: list[str],
) -> dict[str, Any]:
    return {
        **_task_identity(task),
        "root_cause_category": MISSING_EVIDENCE_CATEGORY,
        "root_cause_scope": "local_evidence_gap",
        "supporting_evidence": {"load_errors": sorted(set(errors))},
        "determinism": {
            "stored_evidence_recalculates_identically": False,
            "interpretation": "Cannot determine without the required immutable local evidence.",
        },
        "retry_justified_now": False,
        "code_fix_required": False,
        "disposition": "leave_blocked_unresolved",
        "next_action": "Restore or locate the immutable source evidence before any retry.",
        "evidence_complete": False,
    }


def _failed_decision(category: str) -> dict[str, Any]:
    if category == OPEN_PREVIOUS_CLOSE_CATEGORY:
        action = (
            "Keep quarantined. Escalate the KBS open-field fallback semantics with the "
            "symbol/date evidence; re-ingest only after corrected source evidence exists."
        )
    elif category == CLOSE_RANGE_CATEGORY:
        action = (
            "Keep quarantined. Obtain authoritative KBS close/high/low and adjustment semantics "
            "or verify an alternate official source under a new provenance contract."
        )
    elif category == MIXED_RANGE_CATEGORY:
        action = (
            "Keep quarantined. Resolve both KBS open fallback and close-range semantics before "
            "considering a provenance-preserving re-ingestion."
        )
    else:
        return {
            "code_fix_required": category == NO_REPRODUCTION_CATEGORY,
            "disposition": "leave_blocked_unresolved",
            "next_action": (
                "Investigate the unexplained evidence mismatch; do not retry or publish the task."
            ),
        }
    return {
        "code_fix_required": False,
        "disposition": "quarantine_and_exclude_from_assembly",
        "next_action": action,
    }


def _stale_decision(category: str) -> dict[str, str]:
    if category == HISTORICAL_STALE_CATEGORY:
        return {
            "disposition": "exclude_from_assembly_and_leave_blocked",
            "conditional_retry_trigger": (
                "Provider backfill change or authoritative evidence that observations should exist."
            ),
            "next_action": (
                "Keep blocked. Seek authoritative trading-status/calendar evidence for the tail; "
                "retry only if the source later backfills it."
            ),
        }
    if category == CURRENT_STALE_CATEGORY:
        return {
            "disposition": "exclude_from_assembly_and_leave_blocked",
            "conditional_retry_trigger": "A new provider observation after the campaign edge.",
            "next_action": (
                "Keep blocked and monitor for a new observation or authoritative current trading "
                "status before a bounded retry."
            ),
        }
    if category == POSSIBLE_TRUNCATION_CATEGORY:
        return {
            "disposition": "leave_blocked_unresolved",
            "conditional_retry_trigger": "A smaller diagnostic request proves row-cap truncation.",
            "next_action": "Test a smaller overlapping request before changing chunk boundaries.",
        }
    return {
        "disposition": "leave_blocked_unresolved",
        "conditional_retry_trigger": "New authoritative or provider evidence.",
        "next_action": (
            "Investigate the unresolved edge evidence without weakening the staleness contract."
        ),
    }


def _summarize(
    plan: DailyCampaignPlan,
    state: DailyCampaignState,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    categories = sorted({str(record["root_cause_category"]) for record in records})
    category_counts = {}
    for category in categories:
        selected = [record for record in records if record["root_cause_category"] == category]
        category_counts[category] = {
            "task_count": len(selected),
            "symbol_count": len({str(record["symbol"]) for record in selected}),
            "affected_row_count": sum(
                len(record["supporting_evidence"].get("affected_rows", []))
                for record in selected
            ),
            "next_action": selected[0]["next_action"],
        }
    failed = [record for record in records if record["state_status"] == "failed"]
    stale = [record for record in records if record["state_status"] == "stale"]
    relation_counts: Counter[str] = Counter()
    for record in failed:
        relation_counts.update(record["supporting_evidence"].get("relation_counts", {}))
    failed_task_symbols = {str(record["symbol"]) for record in failed}
    stale_task_symbols = {str(record["symbol"]) for record in stale}
    unresolved_symbols = failed_task_symbols | stale_task_symbols
    return {
        "campaign_task_count": len(plan.tasks),
        "campaign_symbol_count": len(plan.symbols),
        "classified_task_count": len(records),
        "failed_task_count": len(failed),
        "failed_symbol_count": state.symbol_counts.get("failed", 0),
        "symbols_with_failed_tasks": len(failed_task_symbols),
        "stale_task_count": len(stale),
        "stale_symbol_count": state.symbol_counts.get("stale", 0),
        "symbols_with_stale_tasks": len(stale_task_symbols),
        "symbols_with_both_failed_and_stale_tasks": len(
            failed_task_symbols & stale_task_symbols
        ),
        "not_ingested_symbol_count": len(unresolved_symbols),
        "category_counts": category_counts,
        "ohlc_relation_counts": dict(sorted(relation_counts.items())),
        "failed_affected_row_count": sum(
            len(record["supporting_evidence"].get("affected_rows", []))
            for record in failed
        ),
        "tasks_with_complete_local_evidence": sum(
            bool(record["evidence_complete"]) for record in records
        ),
        "tasks_missing_required_local_evidence": sum(
            not bool(record["evidence_complete"]) for record in records
        ),
        "tasks_eligible_for_immediate_retry": sum(
            bool(record["retry_justified_now"]) for record in records
        ),
        "tasks_requiring_code_fix": sum(
            bool(record["code_fix_required"]) for record in records
        ),
        "campaign_impact": {
            "task_counts": state.task_counts,
            "symbol_counts": state.symbol_counts,
            "campaign_complete": state.campaign_complete,
            "assembly_compatible": state.assembly_compatible,
            "assembly_ready": state.assembly_ready,
            "coverage_quality_status": state.coverage_quality_status.value,
            "research_readiness_status": state.research_readiness_status.value,
            "canonical_candidate": state.canonical_candidate,
        },
    }


def _task_identity(task: DailyCampaignTaskAssessment) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "symbol": task.symbol,
        "start_date": task.start_date.isoformat(),
        "end_date": task.end_date.isoformat(),
        "state_status": task.status.value,
        "selected_run_id": task.selected_run_id,
        "attempt_run_ids": task.attempt_run_ids,
        "state_reason_codes": task.reason_codes,
    }


def _source_artifacts(source: _SourceEvidence) -> dict[str, Any]:
    return {
        "manifest_path": str(source.manifest_path),
        "manifest_sha256": _sha256_or_none(source.manifest_path),
        "raw_path": str(source.raw_path) if source.raw_path is not None else None,
        "raw_sha256": _sha256_or_none(source.raw_path),
        "normalized_path": (
            str(source.normalized_path) if source.normalized_path is not None else None
        ),
        "normalized_sha256": _sha256_or_none(source.normalized_path),
        "load_errors": source.load_errors,
    }


def _previous_close_by_date(frame: pd.DataFrame) -> dict[date, float]:
    ordered = frame.copy()
    ordered["date"] = pd.to_datetime(ordered["date"], errors="coerce").dt.date
    ordered = ordered.dropna(subset=["date"]).sort_values("date", kind="stable")
    ordered["previous_close"] = pd.to_numeric(ordered["close"], errors="coerce").shift(1)
    return {
        _as_date(row.date): float(row.previous_close)
        for row in ordered[["date", "previous_close"]].itertuples(index=False)
        if not pd.isna(row.previous_close)
    }


def _next_campaign_observation(
    task: DailyCampaignTaskAssessment,
    state: DailyCampaignState,
    reader: _EvidenceReader,
    last_date: date,
) -> _NextObservation | None:
    later_tasks = sorted(
        (
            candidate
            for candidate in state.tasks
            if candidate.symbol == task.symbol
            and candidate.start_date > task.end_date
            and candidate.selected_run_id is not None
        ),
        key=lambda candidate: candidate.start_date,
    )
    for candidate in later_tasks:
        assert candidate.selected_run_id is not None
        evidence = reader.source(candidate.selected_run_id)
        if evidence.raw is None:
            continue
        dates = sorted(value for value in _frame_dates(evidence.raw) if value > last_date)
        if dates:
            return _NextObservation(
                observation_date=dates[0],
                source_run_id=candidate.selected_run_id,
                source=evidence,
            )
    return None


def _compare_raw_and_normalized(raw: pd.DataFrame, normalized: pd.DataFrame) -> dict[str, Any]:
    raw_values = _comparable_frame(raw, date_column="time")
    normalized_values = _comparable_frame(normalized, date_column="date")
    dates_match = raw_values["date"].tolist() == normalized_values["date"].tolist()
    row_counts_match = len(raw_values) == len(normalized_values)
    max_delta: dict[str, float | None] = {}
    numeric_match = row_counts_match
    if row_counts_match:
        for column in VALUE_COLUMNS:
            delta = (raw_values[column] - normalized_values[column]).abs()
            value = float(delta.max()) if not delta.empty else 0.0
            max_delta[column] = value
            numeric_match = numeric_match and value == 0.0
    else:
        max_delta = dict.fromkeys(VALUE_COLUMNS)
        numeric_match = False
    return {
        "raw_row_count": len(raw_values),
        "normalized_row_count": len(normalized_values),
        "row_counts_match": row_counts_match,
        "dates_match": dates_match,
        "max_absolute_delta": max_delta,
        "exact_numeric_and_date_match": row_counts_match and dates_match and numeric_match,
    }


def _comparable_frame(frame: pd.DataFrame, *, date_column: str) -> pd.DataFrame:
    comparable = pd.DataFrame(
        {"date": pd.to_datetime(frame[date_column], errors="coerce").dt.date}
    )
    for column in VALUE_COLUMNS:
        comparable[column] = pd.to_numeric(frame[column], errors="coerce")
    return comparable.sort_values("date", kind="stable").reset_index(drop=True)


def _frame_dates(frame: pd.DataFrame) -> set[date]:
    column = "date" if "date" in frame.columns else "time"
    parsed = pd.to_datetime(frame[column], errors="coerce").dt.date
    return {_as_date(value) for value in parsed.dropna()}


def _raw_values_by_date(raw: pd.DataFrame) -> dict[str, dict[str, float]]:
    comparable = _comparable_frame(raw, date_column="time")
    return {
        _as_date(row.date).isoformat(): {
            column: _as_float(getattr(row, column)) for column in VALUE_COLUMNS
        }
        for row in comparable.itertuples(index=False)
    }


def _edge_evidence(
    raw: pd.DataFrame,
    normalized: pd.DataFrame,
    edge_date: date,
) -> dict[str, Any]:
    raw_values = _raw_values_by_date(raw).get(edge_date.isoformat())
    normalized_values = _comparable_frame(normalized, date_column="date")
    edge = normalized_values[normalized_values["date"] == edge_date]
    stored = None
    if not edge.empty:
        row = edge.iloc[-1]
        stored = {column: _as_float(row[column]) for column in VALUE_COLUMNS}
    return {"date": edge_date.isoformat(), "raw": raw_values, "normalized": stored}


def _violated_relations(values: dict[str, float]) -> list[str]:
    relations = []
    if values["high"] < values["low"]:
        relations.append("high_below_low")
    if values["high"] < values["open"]:
        relations.append("high_below_open")
    if values["high"] < values["close"]:
        relations.append("high_below_close")
    if values["low"] > values["open"]:
        relations.append("low_above_open")
    if values["low"] > values["close"]:
        relations.append("low_above_close")
    return relations


def _render_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    impact = summary["campaign_impact"]
    lines = [
        "# Daily Campaign Forensic Audit",
        "",
        f"Campaign: `{payload['campaign_id']}`",
        "",
        "## Investigation Method",
        "",
        "- Recomputed every failed task from immutable raw JSONL in memory.",
        "- Compared each stale task raw JSONL with its stored normalized Parquet.",
        "- Re-ran the standard OHLC relationship checks without changing any value.",
        "- Inspected later campaign evidence for stale historical resumptions.",
        "- Made no provider calls and did not modify campaign state.",
        "",
        "## Summary",
        "",
        "| Metric | Count |",
        "| --- | ---: |",
        f"| Classified tasks | {summary['classified_task_count']} |",
        f"| Failed tasks | {summary['failed_task_count']} |",
        f"| Failed symbols | {summary['failed_symbol_count']} |",
        f"| Symbols containing failed tasks | {summary['symbols_with_failed_tasks']} |",
        f"| Stale tasks | {summary['stale_task_count']} |",
        f"| Stale symbols | {summary['stale_symbol_count']} |",
        "| Symbols containing both failed and stale tasks | "
        f"{summary['symbols_with_both_failed_and_stale_tasks']} |",
        f"| Not-ingested symbols explained | {summary['not_ingested_symbol_count']} |",
        f"| Failed affected rows | {summary['failed_affected_row_count']} |",
        "| Tasks eligible for immediate retry | "
        f"{summary['tasks_eligible_for_immediate_retry']} |",
        f"| Tasks requiring a code fix | {summary['tasks_requiring_code_fix']} |",
        "| Tasks missing required local evidence | "
        f"{summary['tasks_missing_required_local_evidence']} |",
        "",
        "## Root-Cause Categories",
        "",
        "| Category | Tasks | Symbols | Affected rows | Next action |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for category, counts in summary["category_counts"].items():
        lines.append(
            f"| `{category}` | {counts['task_count']} | {counts['symbol_count']} | "
            f"{counts['affected_row_count']} | {counts['next_action']} |"
        )
    lines.extend(
        [
            "",
            "## Campaign Impact",
            "",
            f"- Campaign complete: `{impact['campaign_complete']}`",
            f"- Assembly compatible: `{impact['assembly_compatible']}`",
            f"- Assembly ready: `{impact['assembly_ready']}`",
            f"- Coverage quality: `{impact['coverage_quality_status']}`",
            f"- Research readiness: `{impact['research_readiness_status']}`",
            f"- Canonical candidate: `{impact['canonical_candidate']}`",
            "",
            "## Task Index",
            "",
            "Complete row evidence, source hashes, and per-task actions are in the JSON report.",
            "",
            "| Task | Status | Category | Evidence complete | Retry now | Disposition |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for record in payload["tasks"]:
        lines.append(
            f"| `{record['task_id']}` | {record['state_status']} | "
            f"`{record['root_cause_category']}` | {record['evidence_complete']} | "
            f"{record['retry_justified_now']} | {record['disposition']} |"
        )
    lines.extend(
        [
            "",
            "## Explicit Unknowns",
            "",
            "- KBS adjustment and close-field session semantics remain unverified.",
            "- Stale edges lack authoritative historical listing, halt, suspension, and "
            "trading-calendar evidence.",
            "- No OHLC values were repaired, inferred, forward-filled, or accepted under a "
            "weaker contract.",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    _write_text_atomic(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _sha256_or_none(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_date(value: Any) -> date:
    return pd.Timestamp(value).date()


def _as_float(value: Any) -> float:
    return float(value)
