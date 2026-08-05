from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from hose_quant.data.contracts import DAILY_COVERAGE_CONTRACT_VERSION
from hose_quant.data.models import DailyCoverageConfig, DailyCoverageStatus
from hose_quant.data.unit_provenance import resolve_daily_unit_policy

KNOWN_COVERAGE_RISKS = [
    "weekday_calendar_does_not_remove_vietnam_holidays_or_exchange_closures",
    "price_adjustment_semantics_unverified",
    "corporate_action_completeness_unverified",
    "historical_universe_membership_unverified",
]


def audit_daily_coverage(
    daily: pd.DataFrame,
    *,
    current_universe_symbols: set[str],
    requested_symbols: set[str],
    universe_snapshot_date: date,
    daily_run_id: str,
    start: date,
    end: date,
    config: DailyCoverageConfig,
) -> pd.DataFrame:
    if start > end:
        raise ValueError("Coverage start date must not be after end date.")
    required = {"symbol", "date", "open", "high", "low", "close", "volume"}
    missing = sorted(required - set(map(str, daily.columns)))
    if missing:
        raise ValueError(f"Normalized daily coverage input is missing: {', '.join(missing)}.")

    working = daily.copy()
    working["symbol"] = working["symbol"].astype("string").str.strip().str.upper()
    working["date"] = pd.to_datetime(working["date"], errors="coerce").dt.normalize()
    for column in ["open", "high", "low", "close", "volume"]:
        working[column] = pd.to_numeric(working[column], errors="coerce")
    date_in_scope = working["date"].between(
        pd.Timestamp(start), pd.Timestamp(end), inclusive="both"
    )
    working = working[date_in_scope | working["date"].isna()].copy()

    normalized_universe = {
        symbol.strip().upper() for symbol in current_universe_symbols if symbol.strip()
    }
    normalized_requested = {
        symbol.strip().upper() for symbol in requested_symbols if symbol.strip()
    }
    observed_symbols = set(working["symbol"].dropna().astype(str).tolist())
    symbols = sorted(normalized_universe | normalized_requested | observed_symbols)
    requested_weekdays = pd.bdate_range(start=pd.Timestamp(start), end=pd.Timestamp(end))
    requested_weekday_set = set(requested_weekdays)
    rows: list[dict[str, Any]] = []

    for symbol in symbols:
        group = working[working["symbol"] == symbol].copy()
        policy = resolve_daily_unit_policy(group)
        unique_dates = pd.DatetimeIndex(sorted(group["date"].dropna().unique()))
        observed_weekdays = {value for value in unique_dates if value.dayofweek < 5}
        first_observation = unique_dates.min() if len(unique_dates) else pd.NaT
        last_observation = unique_dates.max() if len(unique_dates) else pd.NaT
        requested_observed_count = len(observed_weekdays & requested_weekday_set)
        requested_coverage = (
            requested_observed_count / len(requested_weekdays)
            if len(requested_weekdays)
            else None
        )

        if len(unique_dates):
            span_weekdays = pd.bdate_range(start=first_observation, end=last_observation)
            span_observed = observed_weekdays & set(span_weekdays)
            span_missing = [value for value in span_weekdays if value not in span_observed]
            span_coverage = len(span_observed) / len(span_weekdays) if len(span_weekdays) else None
            longest_missing_streak = _longest_missing_streak(span_weekdays, span_observed)
            stale_days = max((end - last_observation.date()).days, 0)
        else:
            span_weekdays = pd.DatetimeIndex([])
            span_missing = []
            span_coverage = None
            longest_missing_streak = 0
            stale_days = None

        duplicate_row_count = int(group.loc[group["date"].notna(), "date"].duplicated().sum())
        conflicting_duplicate_count = _conflicting_duplicate_date_count(group)
        invalid_date_count = int(group["date"].isna().sum())
        missing_ohlc = group[["open", "high", "low", "close"]].isna().any(axis=1)
        invalid_ohlc = (
            (group["high"] < group["low"])
            | (group["high"] < group["open"])
            | (group["high"] < group["close"])
            | (group["low"] > group["open"])
            | (group["low"] > group["close"])
            | (group[["open", "high", "low", "close"]] < 0).any(axis=1)
        )
        volume = group["volume"]
        valid_volume_count = int(volume.notna().sum())
        zero_volume_count = int((volume == 0).sum())
        zero_volume_frequency = (
            zero_volume_count / valid_volume_count if valid_volume_count else None
        )
        non_integer_volume = volume.notna() & ((volume % 1) != 0)
        weekend_count = int((group["date"].dropna().dt.dayofweek >= 5).sum())
        source_file_count = (
            int(group["__input_path"].dropna().nunique())
            if "__input_path" in group.columns
            else 0
        )
        stale = stale_days is not None and stale_days > config.stale_after_calendar_days
        requested_by_source_run = symbol in normalized_requested

        blockers = {
            "observed_symbol_not_declared_in_source_manifest": (
                len(group) if not requested_by_source_run and not group.empty else 0
            ),
            "invalid_dates": invalid_date_count,
            "duplicate_symbol_date_rows": duplicate_row_count,
            "conflicting_duplicate_values": conflicting_duplicate_count,
            "missing_ohlc_values": int(missing_ohlc.sum()),
            "invalid_ohlc_relationships": int(invalid_ohlc.sum()),
            "missing_volume_values": int(volume.isna().sum()),
            "negative_volume_values": int((volume < 0).sum()),
            "non_integer_volume_values": int(non_integer_volume.sum()),
            "weekend_observations": weekend_count,
        }
        blocking_reasons = [reason for reason, count in blockers.items() if count]
        reasons: list[str] = []
        if group.empty and not requested_by_source_run:
            status = DailyCoverageStatus.NOT_INGESTED
            reasons.append("symbol_not_requested_in_source_run")
        elif group.empty:
            status = DailyCoverageStatus.ABSENT
            reasons.append("requested_symbol_has_no_daily_observations")
        elif blocking_reasons:
            status = DailyCoverageStatus.BLOCKING_QUALITY_ISSUES
            reasons.extend(blocking_reasons)
        elif stale:
            status = DailyCoverageStatus.STALE
            reasons.append("last_observation_is_stale")
        elif len(unique_dates) < config.min_history_observations:
            status = DailyCoverageStatus.INSUFFICIENT_HISTORY
            reasons.append("history_observations_below_minimum")
        elif span_coverage is None or span_coverage < config.min_span_coverage_ratio:
            status = DailyCoverageStatus.SPARSE
            reasons.append("observed_span_coverage_below_minimum")
        elif (
            zero_volume_frequency is None
            or zero_volume_frequency > config.max_zero_volume_frequency
        ):
            status = DailyCoverageStatus.SPARSE
            reasons.append("zero_volume_frequency_above_maximum")
        elif policy.can_compute_vnd:
            status = DailyCoverageStatus.USABLE_VND
            reasons.extend(["raw_ohlcv_coverage_passed", "vnd_unit_provenance_verified"])
        else:
            status = DailyCoverageStatus.USABLE_NON_MONETARY
            reasons.extend(["raw_ohlcv_coverage_passed", "vnd_unit_provenance_unverified"])

        raw_usable = status in {
            DailyCoverageStatus.USABLE_VND,
            DailyCoverageStatus.USABLE_NON_MONETARY,
        }
        rows.append(
            {
                "feature_input_contract_version": DAILY_COVERAGE_CONTRACT_VERSION,
                "symbol": symbol,
                "universe_snapshot_date": pd.Timestamp(universe_snapshot_date),
                "current_universe_snapshot_status": (
                    "current_snapshot_candidate"
                    if symbol in normalized_universe
                    else "not_in_selected_current_snapshot"
                ),
                "source_run_request_status": (
                    "requested"
                    if requested_by_source_run
                    else (
                        "not_requested_but_observed"
                        if not group.empty
                        else "not_requested"
                    )
                ),
                "daily_run_id": daily_run_id,
                "requested_start_date": pd.Timestamp(start),
                "requested_end_date": pd.Timestamp(end),
                "first_observation_date": first_observation,
                "last_observation_date": last_observation,
                "observation_count": len(group),
                "unique_observation_date_count": len(unique_dates),
                "duplicate_row_count": duplicate_row_count,
                "conflicting_duplicate_date_count": conflicting_duplicate_count,
                "source_file_count": source_file_count,
                "requested_weekday_count": len(requested_weekdays),
                "requested_weekday_coverage_ratio": requested_coverage,
                "observed_span_weekday_count": len(span_weekdays),
                "observed_span_missing_weekday_count": len(span_missing),
                "observed_span_coverage_ratio": span_coverage,
                "longest_missing_weekday_streak": longest_missing_streak,
                "weekend_observation_count": weekend_count,
                "invalid_date_count": invalid_date_count,
                "missing_ohlc_count": int(missing_ohlc.sum()),
                "invalid_ohlc_count": int(invalid_ohlc.sum()),
                "missing_volume_count": int(volume.isna().sum()),
                "negative_volume_count": int((volume < 0).sum()),
                "non_integer_volume_count": int(non_integer_volume.sum()),
                "zero_volume_count": zero_volume_count,
                "zero_volume_frequency": zero_volume_frequency,
                "stale_calendar_days": stale_days,
                "stale": stale,
                "minimum_history_observations": config.min_history_observations,
                "minimum_span_coverage_ratio": config.min_span_coverage_ratio,
                "maximum_zero_volume_frequency": config.max_zero_volume_frequency,
                "provider": policy.provider,
                "data_backend": policy.data_backend,
                "unit_provenance_status": policy.provenance_status.value,
                "unit_verification_status": policy.verification_status.value,
                "unit_policy_name": policy.name,
                "unit_policy_version": policy.version,
                "price_unit": policy.price_unit.value,
                "volume_unit": policy.volume_unit.value,
                "traded_value_unit": policy.traded_value_unit.value,
                "unit_evidence_reference": policy.evidence_reference,
                "unit_verification_reason": policy.verification_reason,
                "vnd_traded_value_permitted": policy.vnd_traded_value_permitted,
                "raw_ohlcv_research_usable": raw_usable,
                "vnd_liquidity_research_usable": raw_usable and policy.can_compute_vnd,
                "adjusted_price_research_usable": False,
                "point_in_time_universe_research_usable": False,
                "coverage_status": status.value,
                "coverage_reasons": json.dumps(reasons, separators=(",", ":")),
                "known_risks": json.dumps(KNOWN_COVERAGE_RISKS, separators=(",", ":")),
            }
        )

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(
        ["coverage_status", "symbol"], kind="stable"
    ).reset_index(drop=True)


def summarize_daily_coverage(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "audited_symbol_count": 0,
            "current_snapshot_symbol_count": 0,
            "source_run_requested_symbol_count": 0,
            "symbols_with_daily_data": 0,
            "raw_ohlcv_usable_symbol_count": 0,
            "vnd_liquidity_usable_symbol_count": 0,
            "current_snapshot_vnd_usable_symbol_count": 0,
            "status_counts": {},
            "provenance_status_counts": {},
            "raw_ohlcv_usable_symbols": [],
            "vnd_liquidity_usable_symbols": [],
            "current_snapshot_vnd_usable_symbols": [],
            "source_run_requested_symbols": [],
            "common_vnd_overlap_available": False,
            "common_vnd_start_date": None,
            "common_vnd_end_date": None,
        }
    present = frame[frame["observation_count"] > 0]
    vnd_usable = frame[frame["vnd_liquidity_research_usable"].astype(bool)]
    current_vnd_usable = vnd_usable[
        vnd_usable["current_universe_snapshot_status"] == "current_snapshot_candidate"
    ]
    common_start = current_vnd_usable["first_observation_date"].max()
    common_end = current_vnd_usable["last_observation_date"].min()
    common_overlap = (
        not pd.isna(common_start)
        and not pd.isna(common_end)
        and pd.Timestamp(common_start) <= pd.Timestamp(common_end)
    )
    return {
        "audited_symbol_count": len(frame),
        "current_snapshot_symbol_count": int(
            (frame["current_universe_snapshot_status"] == "current_snapshot_candidate").sum()
        ),
        "source_run_requested_symbol_count": int(
            (frame["source_run_request_status"] == "requested").sum()
        ),
        "symbols_with_daily_data": len(present),
        "raw_ohlcv_usable_symbol_count": int(frame["raw_ohlcv_research_usable"].sum()),
        "vnd_liquidity_usable_symbol_count": int(
            frame["vnd_liquidity_research_usable"].sum()
        ),
        "current_snapshot_vnd_usable_symbol_count": len(current_vnd_usable),
        "status_counts": {
            str(key): int(value)
            for key, value in frame["coverage_status"].value_counts().to_dict().items()
        },
        "provenance_status_counts": {
            str(key): int(value)
            for key, value in frame["unit_provenance_status"].value_counts().to_dict().items()
        },
        "raw_ohlcv_usable_symbols": sorted(
            frame.loc[frame["raw_ohlcv_research_usable"].astype(bool), "symbol"]
            .astype(str)
            .tolist()
        ),
        "vnd_liquidity_usable_symbols": sorted(
            vnd_usable["symbol"].astype(str).tolist()
        ),
        "current_snapshot_vnd_usable_symbols": sorted(
            current_vnd_usable["symbol"].astype(str).tolist()
        ),
        "source_run_requested_symbols": sorted(
            frame.loc[frame["source_run_request_status"] == "requested", "symbol"]
            .astype(str)
            .tolist()
        ),
        "earliest_observation_date": _iso_date(present["first_observation_date"].min()),
        "latest_observation_date": _iso_date(present["last_observation_date"].max()),
        "common_vnd_overlap_available": common_overlap,
        "common_vnd_start_date": _iso_date(common_start) if common_overlap else None,
        "common_vnd_end_date": _iso_date(common_end) if common_overlap else None,
    }


def write_daily_coverage_report(
    frame: pd.DataFrame,
    *,
    json_path: Path,
    markdown_path: Path,
    parameters: dict[str, Any],
) -> tuple[Path, Path]:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    summary = summarize_daily_coverage(frame)
    rows = json.loads(frame.to_json(orient="records", date_format="iso"))
    payload = {
        "contract_version": DAILY_COVERAGE_CONTRACT_VERSION,
        "parameters": parameters,
        "summary": summary,
        "rows": rows,
    }
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "# Daily Coverage Audit",
        "",
        "Coverage uses weekdays only. Vietnamese holidays, exchange closures, symbol halts,",
        "adjusted-price semantics, corporate actions, and historical universe membership remain",
        "unverified unless stated otherwise.",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
    ]
    for key, value in summary.items():
        if isinstance(value, (dict, list)):
            continue
        lines.append(f"| {key} | {value if value is not None else 'n/a'} |")
    status_counts = summary["status_counts"]
    lines.extend(["", "## Status Counts", "", "| Status | Symbols |", "| --- | ---: |"])
    for status, count in sorted(status_counts.items()):
        lines.append(f"| {status} | {count} |")
    usable_symbols = summary["current_snapshot_vnd_usable_symbols"]
    lines.extend(
        [
            "",
            "## Verified VND-Usable Current Snapshot Symbols",
            "",
            ", ".join(usable_symbols) if usable_symbols else "None.",
        ]
    )
    lines.extend(
        [
            "",
            "## Symbol Coverage",
            "",
            "| Symbol | Current snapshot | Source request | Status | Rows | First | Last | "
            "Span coverage | "
            "Provenance | VND usable |",
            "| --- | --- | --- | --- | ---: | --- | --- | ---: | --- | --- |",
        ]
    )
    for row in frame.itertuples(index=False):
        coverage = (
            "n/a"
            if pd.isna(row.observed_span_coverage_ratio)
            else f"{row.observed_span_coverage_ratio:.3f}"
        )
        lines.append(
            f"| {row.symbol} | {row.current_universe_snapshot_status} | "
            f"{row.source_run_request_status} | {row.coverage_status} | "
            f"{row.observation_count} | "
            f"{_display_date(row.first_observation_date)} | "
            f"{_display_date(row.last_observation_date)} | {coverage} | "
            f"{row.unit_provenance_status} | {row.vnd_liquidity_research_usable} |"
        )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def _conflicting_duplicate_date_count(frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    value_columns = ["open", "high", "low", "close", "volume"]
    conflicts = 0
    for _date, group in frame.dropna(subset=["date"]).groupby("date"):
        if len(group) > 1 and len(group[value_columns].drop_duplicates()) > 1:
            conflicts += 1
    return conflicts


def _longest_missing_streak(
    expected: pd.DatetimeIndex,
    observed: set[pd.Timestamp],
) -> int:
    longest = current = 0
    for value in expected:
        if value in observed:
            current = 0
        else:
            current += 1
            longest = max(longest, current)
    return longest


def _iso_date(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date().isoformat()


def _display_date(value: Any) -> str:
    return _iso_date(value) or "n/a"
