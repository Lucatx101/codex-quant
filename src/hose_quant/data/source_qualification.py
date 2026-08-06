from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from hose_quant.data.contracts import VCI_SOURCE_QUALIFICATION_CONTRACT_VERSION
from hose_quant.data.manifests import build_manifest, write_manifest
from hose_quant.data.models import (
    DailyCampaignPlan,
    DailyCampaignState,
    DailyUnitProvenance,
    LiquidityUnitPolicy,
    ValidationResult,
    ValidationSeverity,
    utc_now,
)
from hose_quant.data.normalizers import normalize_daily_ohlcv
from hose_quant.data.storage import DataStorage
from hose_quant.data.unit_provenance import (
    VNSTOCK_KBS_DAILY_UNIT_PROVENANCE,
    VNSTOCK_VCI_DAILY_UNIT_PROVENANCE,
    VNSTOCK_VCI_DATA_BACKEND,
    resolve_daily_unit_policy,
)
from hose_quant.data.validators import has_blocking_errors, validate_daily_ohlcv
from hose_quant.data.vnstock_adapter import DAILY_OHLCV_COLUMNS

PRICE_COLUMNS = ("open", "high", "low", "close")
VALUE_COLUMNS = (*PRICE_COLUMNS, "volume")
REQUIRED_RAW_COLUMNS = ("time", *VALUE_COLUMNS)
VCI_QUALIFICATION_DOCUMENTATION = [
    "https://vnstocks.com/docs/vnstock-data/data-sources",
    "https://vnstocks.com/docs/vnstock-data/market-layer-v3",
    "https://vnstocks.com/docs/vnstock/du-lieu-thi-truong-market-data",
    "https://vnstocks.com/docs/tai-lieu/lich-su-phien-ban",
]


@dataclass(frozen=True)
class VciQualificationProbe:
    probe_id: str
    case_class: str
    symbol: str
    start: date
    end: date
    count: int
    expected_empty: bool = False
    determinism_group: str | None = None
    required_boundary_dates: tuple[date, ...] = ()
    aggregate_comparison: bool = True
    purpose: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "probe_id": self.probe_id,
            "case_class": self.case_class,
            "symbol": self.symbol,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "count": self.count,
            "expected_empty": self.expected_empty,
            "determinism_group": self.determinism_group,
            "required_boundary_dates": [
                value.isoformat() for value in self.required_boundary_dates
            ],
            "aggregate_comparison": self.aggregate_comparison,
            "purpose": self.purpose,
        }


VCI_QUALIFICATION_PROBES = (
    VciQualificationProbe(
        probe_id="fpt-short-a",
        case_class="clean_boundary_and_determinism",
        symbol="FPT",
        start=date(2026, 7, 1),
        end=date(2026, 7, 10),
        count=30,
        determinism_group="fpt-short-repeat",
        required_boundary_dates=(date(2026, 7, 1), date(2026, 7, 10)),
        aggregate_comparison=False,
        purpose="Measure start/end behavior, ordering, schema, and the first repeat response.",
    ),
    VciQualificationProbe(
        probe_id="fpt-short-b",
        case_class="clean_boundary_and_determinism",
        symbol="FPT",
        start=date(2026, 7, 1),
        end=date(2026, 7, 10),
        count=30,
        determinism_group="fpt-short-repeat",
        required_boundary_dates=(date(2026, 7, 1), date(2026, 7, 10)),
        aggregate_comparison=False,
        purpose="Repeat the identical request to test deterministic wrapper output.",
    ),
    VciQualificationProbe(
        probe_id="fpt-count-1000",
        case_class="row_limit",
        symbol="FPT",
        start=date(2020, 1, 1),
        end=date(2026, 8, 4),
        count=1000,
        aggregate_comparison=False,
        purpose="Establish behavior at the existing KBS safety boundary.",
    ),
    VciQualificationProbe(
        probe_id="fpt-count-1200",
        case_class="row_limit",
        symbol="FPT",
        start=date(2020, 1, 1),
        end=date(2026, 8, 4),
        count=1200,
        purpose="Detect a silent 1,000-row cap without probing an unbounded history request.",
    ),
    VciQualificationProbe(
        probe_id="abr-kbs-failed",
        case_class="kbs_failed_ohlc",
        symbol="ABR",
        start=date(2020, 1, 1),
        end=date(2021, 12, 30),
        count=600,
        purpose="Compare a representative KBS failed historical task.",
    ),
    VciQualificationProbe(
        probe_id="acl-kbs-failed",
        case_class="kbs_failed_ohlc",
        symbol="ACL",
        start=date(2020, 1, 1),
        end=date(2021, 12, 30),
        count=600,
        purpose="Compare a second KBS failed historical task.",
    ),
    VciQualificationProbe(
        probe_id="khp-kbs-failed-current",
        case_class="kbs_failed_ohlc_current",
        symbol="KHP",
        start=date(2025, 12, 30),
        end=date(2026, 8, 4),
        count=200,
        purpose="Compare a recent KBS OHLC failure.",
    ),
    VciQualificationProbe(
        probe_id="hpx-suspension-stale",
        case_class="suspension_and_kbs_stale",
        symbol="HPX",
        start=date(2023, 8, 15),
        end=date(2024, 4, 30),
        count=250,
        purpose="Observe a known long trading interruption and the KBS stale boundary.",
    ),
    VciQualificationProbe(
        probe_id="btt-sparse-stale",
        case_class="sparse_trading_and_kbs_stale",
        symbol="BTT",
        start=date(2021, 12, 31),
        end=date(2023, 12, 30),
        count=600,
        purpose="Observe sparse trading and a historical stale KBS task.",
    ),
    VciQualificationProbe(
        probe_id="lgc-current-stale",
        case_class="kbs_stale_current",
        symbol="LGC",
        start=date(2025, 12, 30),
        end=date(2026, 8, 4),
        count=200,
        purpose="Compare a current-tail stale KBS task.",
    ),
    VciQualificationProbe(
        probe_id="gee-prelisting-empty",
        case_class="prelisting_empty",
        symbol="GEE",
        start=date(2020, 1, 1),
        end=date(2021, 12, 30),
        count=600,
        expected_empty=True,
        purpose="Verify exact empty-response handling before locally observed listing history.",
    ),
)


class VciQualificationProvider(Protocol):
    call_count: int

    def fetch_daily_ohlcv_from_source(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        source: str,
        count: int,
    ) -> pd.DataFrame: ...

    def daily_unit_provenance_for_source(self, source: str) -> DailyUnitProvenance: ...


@dataclass(frozen=True)
class VciQualificationArtifacts:
    payload: dict[str, Any]
    input_paths: list[Path]
    output_paths: list[Path]
    json_path: Path
    markdown_path: Path
    unit_policy: LiquidityUnitPolicy
    provider_call_count: int
    raw_row_count: int


def qualification_plan() -> list[dict[str, Any]]:
    return [probe.as_dict() for probe in VCI_QUALIFICATION_PROBES]


def execute_vci_source_qualification(
    *,
    provider: VciQualificationProvider,
    storage: DataStorage,
    report_root: Path,
    plan: DailyCampaignPlan,
    state: DailyCampaignState,
    campaign_plan_path: Path,
    campaign_state_path: Path,
    forensic_report_path: Path | None,
    parent_run_id: str,
    started_at_utc: datetime,
    sanitize_provider_error: Callable[[BaseException], str],
    max_retry_attempts: int,
    provider_sleep_seconds: float,
) -> VciQualificationArtifacts:
    provider_calls_at_start = provider.call_count
    source_unit_provenance = provider.daily_unit_provenance_for_source(VNSTOCK_VCI_DATA_BACKEND)
    if source_unit_provenance != VNSTOCK_VCI_DAILY_UNIT_PROVENANCE:
        raise ValueError("Provider VCI unit provenance does not match the registered contract.")
    probe_results: list[dict[str, Any]] = []
    normalized_frames: list[pd.DataFrame] = []
    output_paths: list[Path] = []
    input_paths: set[Path] = {campaign_plan_path, campaign_state_path}
    if forensic_report_path is not None:
        input_paths.add(forensic_report_path)
    stop_after_error = False

    for index, probe in enumerate(VCI_QUALIFICATION_PROBES, start=1):
        if stop_after_error:
            probe_results.append(
                {
                    **probe.as_dict(),
                    "execution_status": "not_executed_after_provider_error",
                    "provider_call_count": 0,
                }
            )
            continue

        child_started = utc_now()
        child_run_id = f"{parent_run_id}-{index:02d}-{probe.probe_id}"
        calls_before_probe = provider.call_count
        child_outputs: list[Path] = []
        validation_results: list[ValidationResult] = []
        errors: list[str] = []
        raw = pd.DataFrame(columns=DAILY_OHLCV_COLUMNS)
        normalized = pd.DataFrame()
        raw_evidence_path: Path | None = None
        analysis: dict[str, Any] = {}
        try:
            raw = provider.fetch_daily_ohlcv_from_source(
                probe.symbol,
                probe.start,
                probe.end,
                source=VNSTOCK_VCI_DATA_BACKEND,
                count=probe.count,
            )
            raw = raw.copy()
            raw_path = storage.raw_dataset_dir("vci_qualification", child_run_id) / "raw.jsonl"
            if raw_path.exists():
                raise ValueError(f"Immutable VCI probe output already exists: {raw_path}.")
            recorded_raw = raw.copy()
            recorded_raw["qualification_probe_id"] = probe.probe_id
            recorded_raw["request_symbol"] = probe.symbol
            recorded_raw["request_start_date"] = probe.start.isoformat()
            recorded_raw["request_end_date"] = probe.end.isoformat()
            recorded_raw["request_count"] = probe.count
            recorded_raw["request_source"] = VNSTOCK_VCI_DATA_BACKEND
            raw_evidence_path = storage.write_raw_frame(
                "vci_qualification", child_run_id, recorded_raw
            )
            child_outputs.append(raw_evidence_path)

            normalized = normalize_daily_ohlcv(
                raw,
                symbol=probe.symbol,
                exchange="HOSE",
                ingestion_timestamp_utc=child_started,
                unit_provenance=source_unit_provenance,
            )
            normalized_frames.append(normalized)
            analysis = inspect_probe_response(raw, normalized, probe=probe)
            requested_window = _filter_normalized_window(
                normalized,
                probe.start,
                probe.end,
            )
            if not requested_window.empty:
                validation_results = validate_daily_ohlcv(requested_window)
            validation_results.append(_empty_expectation_result(probe, raw.empty))
            validation_results.append(_window_expectation_result(probe, len(requested_window)))
        except (Exception, KeyboardInterrupt, SystemExit) as exc:
            errors.append(sanitize_provider_error(exc))
            analysis = {
                "raw_row_count": 0,
                "request_window_row_count": 0,
                "provider_error": errors[0],
            }
            stop_after_error = True

        probe_call_count = provider.call_count - calls_before_probe
        child_status = "failed" if errors or has_blocking_errors(validation_results) else "success"
        child_manifest = build_manifest(
            run_id=child_run_id,
            command="data qualify-vci-source-probe",
            started_at_utc=child_started,
            finished_at_utc=utc_now(),
            status=child_status,
            symbols=[probe.symbol],
            exchange="HOSE",
            start_date=probe.start.isoformat(),
            end_date=probe.end.isoformat(),
            resolution="1D",
            row_counts={
                "raw": len(raw),
                "requested_window": int(analysis.get("request_window_row_count", 0)),
            },
            output_paths=child_outputs,
            parameters={
                "parent_qualification_run_id": parent_run_id,
                "qualification_contract_version": VCI_SOURCE_QUALIFICATION_CONTRACT_VERSION,
                "source": VNSTOCK_VCI_DATA_BACKEND,
                "count": probe.count,
                "expected_empty": probe.expected_empty,
                "aggregate_comparison": probe.aggregate_comparison,
                "case_class": probe.case_class,
                "purpose": probe.purpose,
                "request_semantics_under_test": "start,end,countBack via vnstock Unified UI",
                "declared_source_unit_provenance": (source_unit_provenance.model_dump(mode="json")),
                "response_analysis": analysis,
            },
            unit_provenance=resolve_daily_unit_policy(normalized),
            data_contract_versions={
                "vci_source_qualification": VCI_SOURCE_QUALIFICATION_CONTRACT_VERSION,
            },
            validation_results=validation_results,
            error_summary=errors,
            provider_call_count=probe_call_count,
        )
        child_manifest_path = write_manifest(child_manifest, storage.manifest_root)
        child_outputs.append(child_manifest_path)
        output_paths.extend(child_outputs)

        comparison, comparison_inputs = compare_probe_with_kbs(
            probe=probe,
            vci_normalized=normalized,
            state=state,
            storage=storage,
        )
        input_paths.update(comparison_inputs)
        probe_results.append(
            {
                **probe.as_dict(),
                "execution_status": child_status,
                "probe_run_id": child_run_id,
                "probe_manifest_path": str(child_manifest_path),
                "raw_evidence_path": (
                    str(raw_evidence_path) if raw_evidence_path is not None else None
                ),
                "raw_evidence_sha256": (
                    _file_digest(raw_evidence_path) if raw_evidence_path is not None else None
                ),
                "provider_call_count": probe_call_count,
                "response": analysis,
                "validation": [item.model_dump(mode="json") for item in validation_results],
                "sanitized_errors": errors,
                "kbs_comparison": comparison,
            }
        )

    determinism = _determinism_assessment(probe_results)
    criteria = qualification_criteria(probe_results, determinism=determinism)
    verdict = derive_vci_verdict(criteria)
    comparisons = [item["kbs_comparison"] for item in probe_results if "kbs_comparison" in item]
    adjustment = _adjustment_assessment(comparisons)
    unit_decision = _unit_provenance_decision(normalized_frames)
    supported, constraints, failures = _conclusions(
        probe_results,
        criteria=criteria,
        verdict=verdict,
    )
    recommendation = _phase_2_4_2b_recommendation(verdict)
    package_version = _package_version("vnstock")
    provider_call_count = provider.call_count - provider_calls_at_start
    payload: dict[str, Any] = {
        "qualification_contract_version": VCI_SOURCE_QUALIFICATION_CONTRACT_VERSION,
        "run_id": parent_run_id,
        "campaign_id": plan.campaign_id,
        "campaign_evidence_baseline": {
            "plan_path": str(campaign_plan_path),
            "plan_sha256": _file_digest(campaign_plan_path),
            "state_path": str(campaign_state_path),
            "state_sha256": _file_digest(campaign_state_path),
            "task_counts": state.task_counts,
            "symbol_counts": state.symbol_counts,
            "campaign_complete": state.campaign_complete,
            "assembly_ready": state.assembly_ready,
            "research_readiness_status": state.research_readiness_status.value,
            "phase_2_4_1_forensic_report_path": (
                str(forensic_report_path) if forensic_report_path is not None else None
            ),
            "phase_2_4_1_forensic_report_sha256": (
                _file_digest(forensic_report_path) if forensic_report_path is not None else None
            ),
        },
        "started_at_utc": started_at_utc.isoformat(),
        "finished_at_utc": utc_now().isoformat(),
        "scope": "bounded_vnstock_vci_daily_source_qualification_only",
        "final_verdict": verdict,
        "verdict_scope": "canonical_daily_ohlcv",
        "scoped_verdict": (
            "rejected_for_canonical_daily_ohlcv" if verdict == "rejected" else verdict
        ),
        "blanket_provider_usability_claim": False,
        "provider": "vnstock",
        "data_backend": VNSTOCK_VCI_DATA_BACKEND,
        "vnstock_package_version": package_version,
        "raw_evidence_level": (
            "vnstock_unified_ui_dataframe_after_vci_field_mapping_timezone_conversion_"
            "and_price_scaling"
        ),
        "live_probe_count_planned": len(VCI_QUALIFICATION_PROBES),
        "live_probe_count_executed": sum(
            item["execution_status"] != "not_executed_after_provider_error"
            for item in probe_results
        ),
        "provider_call_count": provider_call_count,
        "wrapper_retry_attempts_max_per_probe": max_retry_attempts,
        "minimum_seconds_between_wrapper_attempts": provider_sleep_seconds,
        "probe_execution_mode": "sequential",
        "repeatability": (
            "Each response has a unique immutable raw path and child manifest; reruns create a "
            "new parent run and never overwrite campaign or prior qualification evidence."
        ),
        "official_api_evidence": {
            "documentation_urls": VCI_QUALIFICATION_DOCUMENTATION,
            "library_method": (
                "Market().equity(symbol).ohlcv(start=..., end=..., resolution='1D', "
                "count=..., source='vci')"
            ),
            "installed_wrapper_request_mapping": {
                "end": "converted to an inclusive end-day timestamp by adding one day",
                "count": "mapped to VCI countBack",
                "start": (
                    "accepted by the public method but not sent in the VCI request payload; "
                    "it only influences default countBack when count is omitted"
                ),
                "pagination": "no page or offset control exposed by this method",
            },
            "wrapper_field_mapping": {
                "t": "time",
                "o": "open",
                "h": "high",
                "l": "low",
                "c": "close",
                "v": "volume",
            },
            "wrapper_output_conversion": {
                "stock_ohlc": "upstream numeric values divided by 1000 and rounded to 2 decimals",
                "volume": "converted to integer without scaling",
                "daily_time": "epoch seconds converted through Asia/Ho_Chi_Minh to a date label",
            },
            "response_headers_exposed": False,
            "requalification_required_after_vnstock_version_change": True,
        },
        "qualification_criteria": criteria,
        "determinism_assessment": determinism,
        "unit_provenance_decision": unit_decision,
        "adjustment_semantics_decision": adjustment,
        "supported_capabilities": supported,
        "discovered_constraints": constraints,
        "unresolved_semantics": [
            "VCI adjusted-versus-unadjusted price semantics remain unknown.",
            "No authoritative corporate-action event calendar was available in local evidence.",
            "The maximum accepted countBack above the bounded 1,200-row probe remains unknown.",
            "Provider-side retry count, HTTP rate-limit headers, and quota state are not exposed.",
            "Historical point-in-time universe membership and tradability remain unverified.",
        ],
        "observed_failure_modes": failures,
        "phase_2_4_2b_recommendation": recommendation,
        "no_source_mixing": True,
        "no_campaign_mutation": True,
        "probes": probe_results,
    }

    report_root.mkdir(parents=True, exist_ok=True)
    json_path = report_root / f"{parent_run_id}.json"
    markdown_path = report_root / f"{parent_run_id}.md"
    _write_text_atomic(json_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    _write_text_atomic(markdown_path, _render_markdown(payload))
    output_paths.extend([json_path, markdown_path])

    all_normalized = (
        pd.concat(normalized_frames, ignore_index=True) if normalized_frames else pd.DataFrame()
    )
    return VciQualificationArtifacts(
        payload=payload,
        input_paths=sorted(input_paths),
        output_paths=output_paths,
        json_path=json_path,
        markdown_path=markdown_path,
        unit_policy=resolve_daily_unit_policy(all_normalized),
        provider_call_count=provider_call_count,
        raw_row_count=sum(
            int(item.get("response", {}).get("raw_row_count", 0)) for item in probe_results
        ),
    )


def inspect_probe_response(
    raw: pd.DataFrame,
    normalized: pd.DataFrame,
    *,
    probe: VciQualificationProbe,
) -> dict[str, Any]:
    raw_columns = set(map(str, raw.columns))
    missing_columns = sorted(set(REQUIRED_RAW_COLUMNS) - raw_columns)
    parsed_time = pd.to_datetime(raw.get("time", pd.Series(dtype="object")), errors="coerce")
    valid_time = parsed_time.dropna()
    normalized_dates = pd.to_datetime(normalized.get("date"), errors="coerce").dt.date
    in_window = normalized_dates.between(probe.start, probe.end, inclusive="both")
    window = normalized.loc[in_window].copy()
    validation_results = validate_daily_ohlcv(normalized) if not normalized.empty else []
    window_validation_results = validate_daily_ohlcv(window) if not window.empty else []
    required_dates = set(probe.required_boundary_dates)
    observed_dates = set(normalized_dates.dropna().tolist())
    response_digest = _frame_digest(raw)
    volume = pd.to_numeric(normalized.get("volume"), errors="coerce")
    window_volume = pd.to_numeric(window.get("volume"), errors="coerce")
    flat_ohlc = (
        normalized[list(PRICE_COLUMNS)].nunique(axis=1, dropna=False).eq(1)
        if set(PRICE_COLUMNS).issubset(normalized.columns)
        else pd.Series(False, index=normalized.index)
    )
    expected_weekdays = pd.bdate_range(probe.start, probe.end)
    return {
        "raw_row_count": len(raw),
        "raw_columns": sorted(raw_columns),
        "raw_schema": {str(column): str(dtype) for column, dtype in raw.dtypes.items()},
        "missing_required_columns": missing_columns,
        "malformed_time_count": int(parsed_time.isna().sum()),
        "earliest_response_date": (
            valid_time.min().date().isoformat() if not valid_time.empty else None
        ),
        "latest_response_date": (
            valid_time.max().date().isoformat() if not valid_time.empty else None
        ),
        "request_window_row_count": len(window),
        "request_window_expected_weekday_count": len(expected_weekdays),
        "rows_before_requested_start": int((normalized_dates < probe.start).sum()),
        "rows_after_requested_end": int((normalized_dates > probe.end).sum()),
        "raw_time_ordering": (
            "ascending" if valid_time.is_monotonic_increasing else "not_ascending"
        ),
        "duplicate_date_count": int(normalized_dates.duplicated().sum()),
        "request_window_duplicate_date_count": int(
            pd.to_datetime(window.get("date", pd.Series(dtype="object")), errors="coerce")
            .dt.date.duplicated()
            .sum()
        ),
        "null_value_counts": {
            column: int(normalized[column].isna().sum())
            for column in VALUE_COLUMNS
            if column in normalized.columns
        },
        "zero_volume_row_count": int(volume.eq(0).sum()),
        "request_window_zero_volume_row_count": int(window_volume.eq(0).sum()),
        "request_window_positive_volume_row_count": int(window_volume.gt(0).sum()),
        "flat_ohlc_row_count": int(flat_ohlc.sum()),
        "boundary_dates_required": sorted(value.isoformat() for value in required_dates),
        "boundary_dates_missing": sorted(
            value.isoformat() for value in required_dates - observed_dates
        ),
        "count_limit_not_exceeded": len(raw) <= probe.count,
        "expected_empty": probe.expected_empty,
        "empty_observed": raw.empty,
        "empty_expectation_matched": raw.empty == probe.expected_empty,
        "blocking_ohlc_check_count": sum(item.blocks_output for item in validation_results),
        "blocking_ohlc_checks": [
            item.check_name for item in validation_results if item.blocks_output
        ],
        "request_window_blocking_ohlc_check_count": sum(
            item.blocks_output for item in window_validation_results
        ),
        "request_window_blocking_ohlc_checks": [
            item.check_name for item in window_validation_results if item.blocks_output
        ],
        "response_value_digest_sha256": response_digest,
        "timestamp_semantics": (
            "vnstock wrapper daily date label; no intraday timestamp or timezone retained"
        ),
    }


def qualification_criteria(
    probes: list[dict[str, Any]],
    *,
    determinism: dict[str, Any],
) -> dict[str, bool]:
    executed = [item for item in probes if item.get("response")]
    expected_nonempty = [item for item in executed if not item["expected_empty"]]
    by_id = {item["probe_id"]: item for item in probes}
    count_1000 = by_id.get("fpt-count-1000", {}).get("response", {})
    count_1200 = by_id.get("fpt-count-1200", {}).get("response", {})
    short = by_id.get("fpt-short-a", {}).get("response", {})
    comparisons = [
        item
        for item in probes
        if item.get("kbs_comparison", {}).get("overlapping_date_count", 0) > 0
    ]
    operational_complete = len(executed) == len(VCI_QUALIFICATION_PROBES) and all(
        not item.get("sanitized_errors") for item in executed
    )
    schema_valid = bool(expected_nonempty) and all(
        item["response"].get("raw_row_count", 0) > 0
        and not item["response"].get("missing_required_columns")
        and item["response"].get("malformed_time_count") == 0
        for item in expected_nonempty
    )
    return {
        "all_bounded_probes_executed": operational_complete,
        "schema_and_date_labels_valid": schema_valid,
        "requested_window_observations_match_probe_expectation": bool(executed)
        and all(
            (
                item["response"].get("request_window_row_count", 0) == 0
                if item["expected_empty"]
                else item["response"].get("request_window_row_count", 0) > 0
            )
            for item in executed
        ),
        "ohlc_invariants_valid": bool(expected_nonempty)
        and all(
            item["response"].get("request_window_blocking_ohlc_check_count") == 0
            for item in expected_nonempty
        ),
        "duplicate_dates_absent": bool(expected_nonempty)
        and all(
            item["response"].get("request_window_duplicate_date_count") == 0
            for item in expected_nonempty
        ),
        "empty_response_behavior_verified": all(
            item.get("response", {}).get("empty_expectation_matched", False)
            for item in probes
            if item.get("expected_empty")
        ),
        "end_inclusivity_verified": not short.get("boundary_dates_missing")
        and len(short.get("boundary_dates_required", [])) == 2,
        "start_semantics_characterized": short.get("rows_before_requested_start", 0) > 0,
        "countback_through_1200_verified": (
            count_1000.get("raw_row_count") == 1000
            and count_1200.get("raw_row_count") == 1200
            and bool(count_1200.get("count_limit_not_exceeded"))
        ),
        "identical_request_deterministic": bool(determinism.get("all_groups_exact")),
        "vci_unit_provenance_registered": _unit_contract_is_registered(),
        "cross_source_comparison_completed": len({item["symbol"] for item in comparisons}) >= 5,
        "adjustment_semantics_verified": False,
    }


def derive_vci_verdict(criteria: dict[str, bool]) -> str:
    if not criteria.get("all_bounded_probes_executed", False):
        return "unknown"
    required = (
        "schema_and_date_labels_valid",
        "requested_window_observations_match_probe_expectation",
        "ohlc_invariants_valid",
        "duplicate_dates_absent",
        "empty_response_behavior_verified",
        "end_inclusivity_verified",
        "start_semantics_characterized",
        "countback_through_1200_verified",
        "identical_request_deterministic",
        "vci_unit_provenance_registered",
        "cross_source_comparison_completed",
    )
    if not all(criteria.get(item, False) for item in required):
        return "rejected"
    if criteria.get("adjustment_semantics_verified", False):
        return "qualified"
    return "qualified_with_constraints"


def compare_probe_with_kbs(
    *,
    probe: VciQualificationProbe,
    vci_normalized: pd.DataFrame,
    state: DailyCampaignState,
    storage: DataStorage,
) -> tuple[dict[str, Any], set[Path]]:
    inputs: set[Path] = set()
    matching_tasks = [
        task
        for task in state.tasks
        if task.symbol == probe.symbol
        and task.start_date <= probe.end
        and task.end_date >= probe.start
    ]
    run_ids = sorted(
        {task.selected_run_id for task in matching_tasks if task.selected_run_id is not None}
    )
    kbs_frames: list[pd.DataFrame] = []
    load_errors: list[str] = []
    evidence: list[dict[str, Any]] = []
    for run_id in run_ids:
        manifest_path = storage.manifest_path(run_id)
        inputs.add(manifest_path)
        try:
            manifest = storage.read_manifest(run_id)
        except (OSError, ValueError) as exc:
            load_errors.append(f"{run_id}:manifest_{type(exc).__name__}")
            continue
        if manifest is None:
            load_errors.append(f"{run_id}:manifest_missing")
            continue
        raw_candidates = [
            Path(value) for value in manifest.output_paths if Path(value).name == "raw.jsonl"
        ]
        raw_candidates.append(storage.raw_dataset_dir("daily", run_id) / "raw.jsonl")
        raw_path = next((path for path in raw_candidates if path.exists()), None)
        if raw_path is None:
            load_errors.append(f"{run_id}:raw_missing")
            continue
        inputs.add(raw_path)
        evidence.append(
            {
                "run_id": run_id,
                "manifest_path": str(manifest_path),
                "manifest_sha256": _file_digest(manifest_path),
                "raw_path": str(raw_path),
                "raw_sha256": _file_digest(raw_path),
            }
        )
        if raw_path.stat().st_size == 0:
            continue
        try:
            raw = pd.read_json(raw_path, lines=True)
        except (OSError, ValueError) as exc:
            load_errors.append(f"{run_id}:raw_{type(exc).__name__}")
            continue
        if "symbol" in raw.columns:
            raw = raw[raw["symbol"].astype(str).str.upper() == probe.symbol]
        if raw.empty:
            continue
        kbs_frames.append(
            normalize_daily_ohlcv(
                raw,
                symbol=probe.symbol,
                exchange="HOSE",
                unit_provenance=VNSTOCK_KBS_DAILY_UNIT_PROVENANCE,
            )
        )

    kbs = pd.concat(kbs_frames, ignore_index=True) if kbs_frames else pd.DataFrame()
    vci = _filter_normalized_window(vci_normalized, probe.start, probe.end)
    kbs = _filter_normalized_window(kbs, probe.start, probe.end)
    kbs_duplicates = int(kbs.duplicated(subset=["symbol", "date"]).sum()) if not kbs.empty else 0
    vci_duplicates = int(vci.duplicated(subset=["symbol", "date"]).sum()) if not vci.empty else 0
    base: dict[str, Any] = {
        "probe_id": probe.probe_id,
        "symbol": probe.symbol,
        "included_in_unique_sample_aggregate": probe.aggregate_comparison,
        "comparison_scope": "series-level evidence only; no canonical row selection",
        "campaign_task_statuses": sorted({task.status.value for task in matching_tasks}),
        "campaign_task_ids": [task.task_id for task in matching_tasks],
        "kbs_source_evidence": evidence,
        "kbs_load_errors": load_errors,
        "kbs_window_row_count": len(kbs),
        "vci_window_row_count": len(vci),
        "kbs_duplicate_date_count": kbs_duplicates,
        "vci_duplicate_date_count": vci_duplicates,
        "overlapping_date_count": 0,
        "kbs_only_date_count": 0,
        "vci_only_date_count": 0,
        "exact_ohlcv_match_count": 0,
        "differing_ohlcv_row_count": 0,
        "price_ratio_vci_over_kbs": {},
        "sample_differences": [],
    }
    if kbs.empty or vci.empty or kbs_duplicates or vci_duplicates:
        return base, inputs

    left = kbs[["date", *VALUE_COLUMNS]].rename(
        columns={column: f"{column}_kbs" for column in VALUE_COLUMNS}
    )
    right = vci[["date", *VALUE_COLUMNS]].rename(
        columns={column: f"{column}_vci" for column in VALUE_COLUMNS}
    )
    merged = left.merge(right, on="date", how="outer", indicator=True).sort_values("date")
    overlap = merged[merged["_merge"] == "both"].copy()
    equal_mask = pd.Series(True, index=overlap.index)
    differing_columns: dict[str, int] = {}
    ratios: dict[str, dict[str, float]] = {}
    for column in VALUE_COLUMNS:
        kbs_values = pd.to_numeric(overlap[f"{column}_kbs"], errors="coerce")
        vci_values = pd.to_numeric(overlap[f"{column}_vci"], errors="coerce")
        same = kbs_values.eq(vci_values) | (kbs_values.isna() & vci_values.isna())
        equal_mask &= same
        differing_columns[column] = int((~same).sum())
        valid_ratio = kbs_values.notna() & vci_values.notna() & kbs_values.ne(0)
        if valid_ratio.any():
            observed = (vci_values[valid_ratio] / kbs_values[valid_ratio]).astype(float)
            ratios[column] = {
                "minimum": float(observed.min()),
                "median": float(observed.median()),
                "maximum": float(observed.max()),
            }
    differing = overlap[~equal_mask]
    samples: list[dict[str, Any]] = []
    for row in differing.head(5).itertuples(index=False):
        sample: dict[str, Any] = {"date": _date_text(row.date)}
        for column in VALUE_COLUMNS:
            sample[f"{column}_kbs"] = _json_scalar(getattr(row, f"{column}_kbs"))
            sample[f"{column}_vci"] = _json_scalar(getattr(row, f"{column}_vci"))
        samples.append(sample)
    base.update(
        {
            "overlapping_date_count": len(overlap),
            "kbs_only_date_count": int((merged["_merge"] == "left_only").sum()),
            "vci_only_date_count": int((merged["_merge"] == "right_only").sum()),
            "exact_ohlcv_match_count": int(equal_mask.sum()),
            "differing_ohlcv_row_count": len(differing),
            "differing_column_counts": differing_columns,
            "price_ratio_vci_over_kbs": ratios,
            "sample_differences": samples,
        }
    )
    return base, inputs


def _filter_normalized_window(frame: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    if frame.empty or "date" not in frame.columns:
        return pd.DataFrame()
    output = frame.copy()
    dates = pd.to_datetime(output["date"], errors="coerce").dt.date
    output = output[dates.between(start, end, inclusive="both")].copy()
    output["date"] = pd.to_datetime(output["date"], errors="coerce").dt.date
    return output.sort_values(["symbol", "date"], kind="stable").reset_index(drop=True)


def _determinism_assessment(probes: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in probes:
        group = item.get("determinism_group")
        if group:
            groups.setdefault(str(group), []).append(item)
    results: list[dict[str, Any]] = []
    for group, items in sorted(groups.items()):
        digests = [item.get("response", {}).get("response_value_digest_sha256") for item in items]
        exact = len(items) >= 2 and None not in digests and len(set(digests)) == 1
        results.append(
            {
                "group": group,
                "probe_ids": [item["probe_id"] for item in items],
                "response_value_digests": digests,
                "exact_value_match": exact,
            }
        )
    return {
        "groups": results,
        "all_groups_exact": bool(results) and all(item["exact_value_match"] for item in results),
    }


def _unit_contract_is_registered() -> bool:
    policy_frame = normalize_daily_ohlcv(
        pd.DataFrame(
            [
                {
                    "time": "2026-01-02",
                    "open": 1,
                    "high": 1,
                    "low": 1,
                    "close": 1,
                    "volume": 1,
                }
            ]
        ),
        symbol="UNIT",
        unit_provenance=VNSTOCK_VCI_DAILY_UNIT_PROVENANCE,
    )
    return resolve_daily_unit_policy(policy_frame).vnd_traded_value_permitted


def _unit_provenance_decision(normalized_frames: list[pd.DataFrame]) -> dict[str, Any]:
    observed = [frame for frame in normalized_frames if not frame.empty]
    combined = pd.concat(observed, ignore_index=True) if observed else pd.DataFrame()
    policy = resolve_daily_unit_policy(combined)
    return {
        "status": policy.verification_status.value,
        "provenance_status": policy.provenance_status.value,
        "provider": "vnstock",
        "data_backend": VNSTOCK_VCI_DATA_BACKEND,
        "price_unit": VNSTOCK_VCI_DAILY_UNIT_PROVENANCE.price_unit.value,
        "volume_unit": VNSTOCK_VCI_DAILY_UNIT_PROVENANCE.volume_unit.value,
        "price_scale_to_vnd": VNSTOCK_VCI_DAILY_UNIT_PROVENANCE.price_scale_to_vnd,
        "volume_scale_to_shares": (VNSTOCK_VCI_DAILY_UNIT_PROVENANCE.volume_scale_to_shares),
        "evidence_reference": VNSTOCK_VCI_DAILY_UNIT_PROVENANCE.evidence_reference,
        "evidence_basis": [
            "Installed vnstock VCI parser maps t/o/h/l/c/v to time/open/high/low/close/volume.",
            (
                "Installed common OHLC wrapper divides stock prices by 1000 and retains "
                "integer volume."
            ),
            "Live response schema and cross-source scale comparisons are recorded per probe.",
        ],
        "vnd_traded_value_permitted_for_homogeneous_vci_rows": (policy.vnd_traded_value_permitted),
        "mixed_kbs_vci_rows_permitted": False,
        "adjustment_semantics": "unknown",
    }


def _adjustment_assessment(comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    observed = [item for item in comparisons if item.get("overlapping_date_count", 0) > 0]
    aggregate = [
        item for item in observed if item.get("included_in_unique_sample_aggregate", False)
    ]
    probe_weighted_overlap = sum(int(item["overlapping_date_count"]) for item in observed)
    probe_weighted_differing = sum(int(item["differing_ohlcv_row_count"]) for item in observed)
    overlap = sum(int(item["overlapping_date_count"]) for item in aggregate)
    differing = sum(int(item["differing_ohlcv_row_count"]) for item in aggregate)
    aggregate_symbols = [str(item.get("symbol")) for item in aggregate]
    price_ratio_medians: dict[str, dict[str, float]] = {}
    materially_non_unity: list[str] = []
    for index, comparison in enumerate(aggregate):
        medians = {
            column: float(values["median"])
            for column, values in comparison.get("price_ratio_vci_over_kbs", {}).items()
            if column in PRICE_COLUMNS and "median" in values
        }
        if not medians:
            continue
        key = str(comparison.get("probe_id", f"comparison-{index + 1}"))
        price_ratio_medians[key] = medians
        if any(abs(value - 1.0) > 0.005 for value in medians.values()):
            materially_non_unity.append(key)
    return {
        "status": "unknown",
        "overlapping_rows_compared": overlap,
        "rows_with_any_kbs_vci_ohlcv_difference": differing,
        "comparison_accounting": (
            "One configured aggregate probe per symbol; repeated and nested FPT probes are "
            "excluded from aggregate counts."
        ),
        "aggregate_probe_ids": [str(item.get("probe_id")) for item in aggregate],
        "aggregate_symbols": aggregate_symbols,
        "aggregate_symbols_unique": len(aggregate_symbols) == len(set(aggregate_symbols)),
        "probe_weighted_overlapping_rows": probe_weighted_overlap,
        "probe_weighted_rows_with_any_ohlcv_difference": probe_weighted_differing,
        "price_ratio_medians_vci_over_kbs": price_ratio_medians,
        "materially_non_unity_price_ratio_comparisons": materially_non_unity,
        "known_corporate_action_dates_with_authoritative_local_evidence": [],
        "decision": (
            "Cross-source equality or divergence cannot prove adjusted versus unadjusted "
            "semantics without an authoritative corporate-action series and provider method."
        ),
        "normalized_adjusted_flag": None,
    }


def _conclusions(
    probes: list[dict[str, Any]],
    *,
    criteria: dict[str, bool],
    verdict: str,
) -> tuple[list[str], list[str], list[str]]:
    supported = [
        "Official vnstock Unified UI dispatches bounded daily OHLCV requests to VCI.",
        "VCI wrapper output has the standard time/open/high/low/close/volume schema.",
        "Homogeneous VCI normalized rows can carry a registered thousand-VND/share contract.",
    ]
    if criteria.get("countback_through_1200_verified"):
        supported.append("The observed endpoint returned distinct 1,000- and 1,200-row histories.")
    if criteria.get("identical_request_deterministic"):
        supported.append("An identical bounded request produced an exact repeat response.")
    if criteria.get("empty_response_behavior_verified"):
        supported.append("The VCI no-data exception maps once to an explicit empty response.")

    constraints = [
        (
            "VCI does not send start in the request payload; callers must size countBack and "
            "filter locally."
        ),
        "Qualification supports at most count=1200; larger requests remain outside evidence.",
        "No pagination or offset is exposed by the qualified method.",
        (
            "The VCI no-data message does not distinguish an empty valid window from an invalid "
            "symbol or time request; probes use symbols already present in campaign evidence."
        ),
        "Adjustment semantics remain unknown and adjusted_flag must remain null.",
        "KBS and VCI rows cannot be mixed or preferred row by row under this qualification.",
        "Provider calls are sequential and retain the configured delay and retry ceiling.",
    ]
    failures: list[str] = []
    for item in probes:
        if item.get("sanitized_errors"):
            failures.append(
                f"{item['probe_id']}: provider error: {'; '.join(item['sanitized_errors'])}"
            )
        response = item.get("response", {})
        if response.get("request_window_blocking_ohlc_checks"):
            failures.append(
                f"{item['probe_id']}: requested-window blocking OHLC checks: "
                f"{', '.join(response['request_window_blocking_ohlc_checks'])}"
            )
        if response and not response.get("empty_expectation_matched", True):
            failures.append(f"{item['probe_id']}: empty/non-empty expectation did not match.")
        if item["case_class"] in {
            "suspension_and_kbs_stale",
            "sparse_trading_and_kbs_stale",
        }:
            zero_count = int(response.get("request_window_zero_volume_row_count", 0))
            vci_only = int(item.get("kbs_comparison", {}).get("vci_only_date_count", 0))
            if zero_count or vci_only:
                failures.append(
                    f"{item['probe_id']}: VCI recorded {zero_count} zero-volume window rows "
                    f"and {vci_only} dates absent from the selected KBS evidence."
                )
    if not failures:
        failures.append("No wrapper-level provider failure occurred in the bounded live probes.")
    if verdict == "unknown":
        failures.append("Qualification is incomplete, so no technical suitability claim is made.")
    return supported, constraints, failures


def _phase_2_4_2b_recommendation(verdict: str) -> dict[str, Any]:
    primary = verdict in {"qualified", "qualified_with_constraints"}
    return {
        "recommendation_class": (
            "primary_source_candidate_only" if primary else "diagnostic_evidence_only"
        ),
        "may_evaluate_as_primary_source": primary,
        "may_evaluate_as_fallback_source": False,
        "may_retain_as_non_authoritative_diagnostic_evidence": True,
        "reason": (
            "Phase 2.4.2B may evaluate a separate VCI-primary campaign design under the recorded "
            "constraints; fallback or row-level source mixing requires a later explicit policy."
            if primary
            else (
                "VCI must not be selected as a canonical daily OHLCV primary or fallback "
                "source while the rejected or unknown qualification criteria remain; the "
                "recorded evidence may be retained for non-authoritative diagnostics."
            )
        ),
        "migration_authorized": False,
    }


def _empty_expectation_result(
    probe: VciQualificationProbe,
    empty_observed: bool,
) -> ValidationResult:
    matched = empty_observed == probe.expected_empty
    return ValidationResult(
        dataset_name="vci_source_qualification",
        severity=ValidationSeverity.INFO if matched else ValidationSeverity.ERROR,
        check_name="probe_empty_expectation",
        message=(
            "Observed empty/non-empty behavior matched the probe plan."
            if matched
            else "Observed empty/non-empty behavior did not match the probe plan."
        ),
        blocks_output=not matched,
    )


def _window_expectation_result(
    probe: VciQualificationProbe,
    window_row_count: int,
) -> ValidationResult:
    matched = window_row_count == 0 if probe.expected_empty else window_row_count > 0
    return ValidationResult(
        dataset_name="vci_source_qualification",
        severity=ValidationSeverity.INFO if matched else ValidationSeverity.ERROR,
        check_name="requested_window_observation_expectation",
        message=(
            "Requested-window observation behavior matched the probe plan."
            if matched
            else "Requested-window observation behavior did not match the probe plan."
        ),
        affected_row_count=window_row_count,
        blocks_output=not matched,
    )


def _frame_digest(frame: pd.DataFrame) -> str:
    columns = [column for column in REQUIRED_RAW_COLUMNS if column in frame.columns]
    canonical = frame[columns].copy().reset_index(drop=True)
    if "time" in canonical.columns:
        canonical["time"] = pd.to_datetime(canonical["time"], errors="coerce").dt.strftime(
            "%Y-%m-%d"
        )
    payload = canonical.to_json(orient="records", date_format="iso", double_precision=15)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _package_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "not-installed"


def _json_scalar(value: Any) -> Any:
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def _date_text(value: Any) -> str:
    return pd.Timestamp(value).date().isoformat()


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# VCI Daily Source Qualification",
        "",
        f"- Run ID: `{payload['run_id']}`",
        f"- Campaign evidence: `{payload['campaign_id']}`",
        f"- Final verdict: **{payload['final_verdict']}**",
        (
            f"- Live probes: {payload['live_probe_count_executed']}/"
            f"{payload['live_probe_count_planned']}"
        ),
        f"- Wrapper provider calls: {payload['provider_call_count']}",
        "- Source mixing: prohibited",
        "- Campaign mutation: none",
        "",
        "## Mechanical Criteria",
        "",
    ]
    for name, passed in payload["qualification_criteria"].items():
        lines.append(f"- `{name}`: {'pass' if passed else 'fail/unknown'}")
    lines.extend(["", "## Probe Results", ""])
    lines.append("| Probe | Case | Symbol | Calls | Raw | Window | OHLC blocks | Status |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---|")
    for probe in payload["probes"]:
        response = probe.get("response", {})
        lines.append(
            "| {probe_id} | {case_class} | {symbol} | {calls} | {raw} | {window} | "
            "{blocks} | {status} |".format(
                probe_id=probe["probe_id"],
                case_class=probe["case_class"],
                symbol=probe["symbol"],
                calls=probe.get("provider_call_count", 0),
                raw=response.get("raw_row_count", 0),
                window=response.get("request_window_row_count", 0),
                blocks=response.get("blocking_ohlc_check_count", 0),
                status=probe["execution_status"],
            )
        )
    for heading, key in [
        ("Supported Capabilities", "supported_capabilities"),
        ("Discovered Constraints", "discovered_constraints"),
        ("Unresolved Semantics", "unresolved_semantics"),
        ("Observed Failure Modes", "observed_failure_modes"),
    ]:
        lines.extend(["", f"## {heading}", ""])
        lines.extend(f"- {item}" for item in payload[key])
    unit = payload["unit_provenance_decision"]
    adjustment = payload["adjustment_semantics_decision"]
    recommendation = payload["phase_2_4_2b_recommendation"]
    lines.extend(
        [
            "",
            "## Semantic Decisions",
            "",
            (
                f"- Units: `{unit['status']}`; prices `{unit['price_unit']}`, "
                f"volume `{unit['volume_unit']}`."
            ),
            (
                "- VND permission for homogeneous VCI rows: "
                f"`{unit['vnd_traded_value_permitted_for_homogeneous_vci_rows']}`."
            ),
            f"- Adjustment semantics: `{adjustment['status']}`; `adjusted_flag=null`.",
            "",
            "## Phase 2.4.2B Recommendation",
            "",
            f"- Class: `{recommendation['recommendation_class']}`",
            f"- Primary-source evaluation: `{recommendation['may_evaluate_as_primary_source']}`",
            f"- Fallback-source evaluation: `{recommendation['may_evaluate_as_fallback_source']}`",
            f"- Migration authorized: `{recommendation['migration_authorized']}`",
            f"- Reason: {recommendation['reason']}",
            "",
        ]
    )
    return "\n".join(lines)
