from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from hose_quant.data.contracts import (
    AVAILABILITY_CONTRACT,
    DAILY_PANEL_CONTRACT,
    LIQUIDITY_CONTRACT,
    RESEARCH_UNIVERSE_CONTRACT,
)
from hose_quant.data.models import (
    HistoricalMembershipStatus,
    ProviderTimeParseStatus,
    TimestampAwarenessStatus,
    TradedValueUnit,
    UnitProvenanceStatus,
    UnitVerificationStatus,
    UniverseDiagnostics,
    ValidationResult,
    ValidationSeverity,
)
from hose_quant.data.unit_provenance import (
    effective_unit_metadata,
    resolve_daily_unit_policy,
)


def _result(
    *,
    dataset_name: str,
    severity: ValidationSeverity,
    check_name: str,
    message: str,
    affected_columns: list[str] | None = None,
    affected_row_count: int = 0,
    sample_affected_keys: list[str] | None = None,
    blocks_output: bool = False,
) -> ValidationResult:
    return ValidationResult(
        dataset_name=dataset_name,
        severity=severity,
        check_name=check_name,
        message=message,
        affected_columns=affected_columns or [],
        affected_row_count=affected_row_count,
        sample_affected_keys=sample_affected_keys or [],
        blocks_output=blocks_output,
    )


def _missing_columns(
    frame: pd.DataFrame,
    *,
    dataset_name: str,
    required_columns: set[str],
) -> list[ValidationResult]:
    missing = sorted(required_columns - set(map(str, frame.columns)))
    if not missing:
        return []
    return [
        _result(
            dataset_name=dataset_name,
            severity=ValidationSeverity.ERROR,
            check_name="required_columns",
            message=f"Missing required columns: {', '.join(missing)}.",
            affected_columns=missing,
            blocks_output=True,
        )
    ]


def _sample_keys(frame: pd.DataFrame, columns: list[str], limit: int = 5) -> list[str]:
    existing = [column for column in columns if column in frame.columns]
    if not existing:
        return []
    return [
        "|".join(str(value) for value in row)
        for row in frame[existing].head(limit).itertuples(index=False, name=None)
    ]


def _numeric_non_negative(
    frame: pd.DataFrame,
    *,
    dataset_name: str,
    columns: list[str],
) -> list[ValidationResult]:
    results: list[ValidationResult] = []
    for column in columns:
        if column not in frame.columns:
            continue
        numeric = pd.to_numeric(frame[column], errors="coerce")
        bad = numeric.notna() & (numeric < 0)
        if bad.any():
            results.append(
                _result(
                    dataset_name=dataset_name,
                    severity=ValidationSeverity.ERROR,
                    check_name=f"{column}_non_negative",
                    message=f"{column} contains negative values.",
                    affected_columns=[column],
                    affected_row_count=int(bad.sum()),
                    sample_affected_keys=_sample_keys(frame[bad], ["symbol", "date", "timestamp"]),
                    blocks_output=True,
                )
            )
    return results


def summarize_validation(results: list[ValidationResult]) -> dict[str, int]:
    summary = {severity.value: 0 for severity in ValidationSeverity}
    summary["blocking"] = 0
    for result in results:
        summary[result.severity.value] += 1
        if result.blocks_output:
            summary["blocking"] += 1
    return summary


def has_blocking_errors(results: list[ValidationResult]) -> bool:
    return any(result.blocks_output for result in results)


def validate_universe_snapshot(
    frame: pd.DataFrame,
    diagnostics: UniverseDiagnostics,
) -> list[ValidationResult]:
    dataset_name = "universe"
    required = {
        "provider",
        "exchange",
        "symbol",
        "organ_name",
        "english_organ_name",
        "security_type",
        "provider_id",
        "snapshot_timestamp_utc",
        "raw_exchange_field",
        "raw_type_field",
    }
    results = _missing_columns(frame, dataset_name=dataset_name, required_columns=required)
    if results:
        return results
    if frame["symbol"].isna().any():
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.ERROR,
                check_name="symbol_not_null",
                message="Universe rows contain null symbols.",
                affected_columns=["symbol"],
                affected_row_count=int(frame["symbol"].isna().sum()),
                blocks_output=True,
            )
        )
    duplicates = frame.duplicated(subset=["symbol"])
    if duplicates.any() or diagnostics.duplicate_symbols:
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.ERROR,
                check_name="duplicate_symbols",
                message="Duplicate symbols detected in universe snapshot.",
                affected_columns=["symbol"],
                affected_row_count=int(duplicates.sum() or diagnostics.duplicate_symbols),
                sample_affected_keys=_sample_keys(frame[duplicates], ["symbol"]),
                blocks_output=True,
            )
        )
    if diagnostics.null_exchange_rows:
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.WARNING,
                check_name="null_exchange_fields",
                message="Provider returned rows with null exchange fields before filtering.",
                affected_columns=["exchange", "raw_exchange_field"],
                affected_row_count=diagnostics.null_exchange_rows,
            )
        )
    results.append(
        _result(
            dataset_name=dataset_name,
            severity=ValidationSeverity.INFO,
            check_name="hose_filtering",
            message=(
                f"Provider returned {diagnostics.total_returned_rows} rows; "
                f"{diagnostics.hose_rows} matched HOSE."
            ),
            affected_row_count=diagnostics.total_returned_rows,
        )
    )
    return results


def validate_daily_ohlcv(frame: pd.DataFrame) -> list[ValidationResult]:
    dataset_name = "daily"
    required = {
        "provider",
        "symbol",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "adjusted_flag",
        "source_resolution",
        "ingestion_timestamp_utc",
    }
    results = _missing_columns(frame, dataset_name=dataset_name, required_columns=required)
    if results:
        return results
    price_columns = ["open", "high", "low", "close"]
    results.extend(_numeric_non_negative(frame, dataset_name=dataset_name, columns=price_columns))
    results.extend(_numeric_non_negative(frame, dataset_name=dataset_name, columns=["volume"]))

    high_low_bad = frame["high"] < frame["low"]
    high_open_close_bad = (frame["high"] < frame["open"]) | (frame["high"] < frame["close"])
    low_open_close_bad = (frame["low"] > frame["open"]) | (frame["low"] > frame["close"])
    for check_name, mask, message in [
        ("high_gte_low", high_low_bad, "Daily high is below low."),
        ("high_gte_open_close", high_open_close_bad, "Daily high is below open or close."),
        ("low_lte_open_close", low_open_close_bad, "Daily low is above open or close."),
    ]:
        if mask.any():
            results.append(
                _result(
                    dataset_name=dataset_name,
                    severity=ValidationSeverity.ERROR,
                    check_name=check_name,
                    message=message,
                    affected_columns=price_columns,
                    affected_row_count=int(mask.sum()),
                    sample_affected_keys=_sample_keys(frame[mask], ["symbol", "date"]),
                    blocks_output=True,
                )
            )

    volume = pd.to_numeric(frame["volume"], errors="coerce")
    non_integer = volume.notna() & ((volume % 1) != 0)
    if non_integer.any():
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.ERROR,
                check_name="volume_integer_like",
                message="Daily volume is not integer-like.",
                affected_columns=["volume"],
                affected_row_count=int(non_integer.sum()),
                sample_affected_keys=_sample_keys(frame[non_integer], ["symbol", "date"]),
                blocks_output=True,
            )
        )

    duplicates = frame.duplicated(subset=["symbol", "date"])
    if duplicates.any():
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.ERROR,
                check_name="duplicate_symbol_date",
                message="Duplicate daily rows detected for symbol/date.",
                affected_columns=["symbol", "date"],
                affected_row_count=int(duplicates.sum()),
                sample_affected_keys=_sample_keys(frame[duplicates], ["symbol", "date"]),
                blocks_output=True,
            )
        )
    sorted_frame = frame.sort_values(["symbol", "date"]).reset_index(drop=True)
    if (
        not frame[["symbol", "date"]]
        .reset_index(drop=True)
        .equals(sorted_frame[["symbol", "date"]])
    ):
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.WARNING,
                check_name="sorted_symbol_date",
                message="Daily rows are not sorted by symbol/date.",
                affected_columns=["symbol", "date"],
            )
        )
    future_limit = date.today() + timedelta(days=1)
    dates = pd.to_datetime(frame["date"], errors="coerce").dt.date
    future = dates > future_limit
    if future.any():
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.ERROR,
                check_name="future_dates",
                message="Daily rows contain dates beyond market-calendar tolerance.",
                affected_columns=["date"],
                affected_row_count=int(future.sum()),
                sample_affected_keys=_sample_keys(frame[future], ["symbol", "date"]),
                blocks_output=True,
            )
        )
    unit_policy = resolve_daily_unit_policy(frame)
    results.append(
        _result(
            dataset_name=dataset_name,
            severity=(
                ValidationSeverity.INFO
                if unit_policy.verification_status is UnitVerificationStatus.VERIFIED
                else ValidationSeverity.WARNING
            ),
            check_name="daily_unit_provenance",
            message=unit_policy.verification_reason,
            affected_columns=["provider", "source_resolution"],
            affected_row_count=len(frame),
        )
    )
    return results


def validate_intraday_bars(frame: pd.DataFrame) -> list[ValidationResult]:
    dataset_name = "intraday"
    required = {
        "provider",
        "symbol",
        "timestamp",
        "trading_date",
        "resolution",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "volume_semantics",
        "bar_status",
        "ingestion_timestamp_utc",
    }
    results = _missing_columns(frame, dataset_name=dataset_name, required_columns=required)
    if results:
        return results
    results.extend(
        _numeric_non_negative(
            frame,
            dataset_name=dataset_name,
            columns=["open", "high", "low", "close", "volume"],
        )
    )
    timestamps = pd.to_datetime(frame["timestamp"], errors="coerce")
    invalid = timestamps.isna()
    if invalid.any():
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.ERROR,
                check_name="timestamp_parse",
                message="Intraday timestamp could not be parsed.",
                affected_columns=["timestamp"],
                affected_row_count=int(invalid.sum()),
                sample_affected_keys=_sample_keys(frame[invalid], ["symbol", "timestamp"]),
                blocks_output=True,
            )
        )
    if getattr(timestamps.dt, "tz", None) is None:
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.WARNING,
                check_name="timestamp_timezone_naive",
                message="Intraday timestamps are timezone-naive.",
                affected_columns=["timestamp"],
            )
        )
    provenance_columns = {
        "provider_timestamp_raw",
        "timestamp_timezone_status",
        "timestamp_interpretation",
        "timestamp_localization_applied",
    }
    missing_provenance = provenance_columns - set(map(str, frame.columns))
    if missing_provenance:
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.WARNING,
                check_name="legacy_timestamp_provenance_missing",
                message=(
                    "Legacy intraday rows do not carry the Phase 2 timestamp provenance fields; "
                    "their timezone semantics remain unresolved."
                ),
                affected_columns=sorted(missing_provenance),
                affected_row_count=len(frame),
            )
        )
    else:
        awareness = frame["timestamp_timezone_status"].astype("string")
        naive_localized = (awareness == TimestampAwarenessStatus.NAIVE.value) & frame[
            "timestamp_localization_applied"
        ].fillna(False).astype(bool)
        if naive_localized.any():
            results.append(
                _result(
                    dataset_name=dataset_name,
                    severity=ValidationSeverity.ERROR,
                    check_name="no_unverified_naive_localization",
                    message="Timezone-naive provider timestamps were localized without evidence.",
                    affected_columns=[
                        "provider_timestamp_raw",
                        "timestamp_timezone_status",
                        "timestamp_localization_applied",
                    ],
                    affected_row_count=int(naive_localized.sum()),
                    sample_affected_keys=_sample_keys(
                        frame[naive_localized], ["symbol", "provider_timestamp_raw"]
                    ),
                    blocks_output=True,
                )
            )
    duplicates = frame.duplicated(subset=["symbol", "resolution", "timestamp"])
    if duplicates.any():
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.ERROR,
                check_name="duplicate_symbol_resolution_timestamp",
                message="Duplicate intraday bars detected.",
                affected_columns=["symbol", "resolution", "timestamp"],
                affected_row_count=int(duplicates.sum()),
                sample_affected_keys=_sample_keys(
                    frame[duplicates], ["symbol", "resolution", "timestamp"]
                ),
                blocks_output=True,
            )
        )
    sorted_frame = frame.sort_values(["symbol", "resolution", "timestamp"]).reset_index(drop=True)
    if (
        not frame[["symbol", "resolution", "timestamp"]]
        .reset_index(drop=True)
        .equals(sorted_frame[["symbol", "resolution", "timestamp"]])
    ):
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.WARNING,
                check_name="sorted_symbol_resolution_timestamp",
                message="Intraday rows are not sorted by symbol/resolution/timestamp.",
                affected_columns=["symbol", "resolution", "timestamp"],
            )
        )

    valid = frame.copy()
    valid["timestamp"] = timestamps
    for (_, _resolution), group in valid.dropna(subset=["timestamp"]).groupby(
        ["symbol", "resolution"]
    ):
        gaps = group.sort_values("timestamp")["timestamp"].diff().dropna()
        if (gaps > pd.Timedelta(minutes=90)).any():
            results.append(
                _result(
                    dataset_name=dataset_name,
                    severity=ValidationSeverity.INFO,
                    check_name="session_break_gaps",
                    message="Intraday bars contain large gaps consistent with session breaks.",
                    affected_columns=["timestamp"],
                    affected_row_count=int((gaps > pd.Timedelta(minutes=90)).sum()),
                )
            )
    return results


def validate_research_universe(
    frame: pd.DataFrame,
    *,
    expected_input_row_count: int,
) -> list[ValidationResult]:
    dataset_name = "research_universe"
    results = _missing_columns(
        frame,
        dataset_name=dataset_name,
        required_columns=set(RESEARCH_UNIVERSE_CONTRACT.required_columns),
    )
    if results:
        return results
    if len(frame) != expected_input_row_count:
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.ERROR,
                check_name="input_rows_auditable",
                message="Prepared universe did not preserve one output row per selected input row.",
                affected_row_count=abs(len(frame) - expected_input_row_count),
                blocks_output=True,
            )
        )
    duplicate_input_rows = frame["input_row_number"].duplicated()
    if duplicate_input_rows.any():
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.ERROR,
                check_name="input_row_number_unique",
                message="Prepared universe contains duplicate input row identifiers.",
                affected_columns=["input_row_number"],
                affected_row_count=int(duplicate_input_rows.sum()),
                blocks_output=True,
            )
        )
    if frame["historical_membership_verified"].fillna(False).astype(bool).any():
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.ERROR,
                check_name="no_false_historical_membership",
                message="Current provider snapshots cannot assert verified historical membership.",
                affected_columns=[
                    "source_snapshot_observed_at_utc",
                    "requested_reference_date",
                    "historical_membership_verified",
                ],
                blocks_output=True,
            )
        )
    requested = pd.to_datetime(frame["requested_reference_date"], errors="coerce").notna()
    status = frame["historical_membership_status"].astype("string")
    incorrect_reference_status = requested & (
        status != HistoricalMembershipStatus.REQUESTED_REFERENCE_UNVERIFIED.value
    )
    observed = pd.to_datetime(frame["source_snapshot_observed_at_utc"], errors="coerce", utc=True)
    same_date = requested & (
        pd.to_datetime(frame["requested_reference_date"], errors="coerce").dt.date
        == observed.dt.date
    )
    incorrect_reference_status &= ~same_date
    if incorrect_reference_status.any():
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.ERROR,
                check_name="requested_reference_is_not_membership_evidence",
                message="Historical reference dates must remain structurally unverified.",
                affected_columns=[
                    "requested_reference_date",
                    "historical_membership_status",
                ],
                affected_row_count=int(incorrect_reference_status.sum()),
                blocks_output=True,
            )
        )
    invalid_snapshot = observed.isna()
    if invalid_snapshot.any():
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.ERROR,
                check_name="snapshot_observation_timestamp",
                message="Universe rows require a valid source snapshot observation timestamp.",
                affected_columns=["source_snapshot_observed_at_utc"],
                affected_row_count=int(invalid_snapshot.sum()),
                blocks_output=True,
            )
        )
    snapshot_awareness = frame["source_snapshot_timezone_status"].astype("string")
    non_aware_snapshot = snapshot_awareness != TimestampAwarenessStatus.AWARE.value
    if non_aware_snapshot.any():
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.ERROR,
                check_name="snapshot_timezone_awareness",
                message=(
                    "Universe snapshot observation timestamps must be source-aware; naive "
                    "values are preserved but not localized."
                ),
                affected_columns=[
                    "source_snapshot_timestamp_raw",
                    "source_snapshot_timezone_status",
                    "source_snapshot_localization_applied",
                ],
                affected_row_count=int(non_aware_snapshot.sum()),
                blocks_output=True,
            )
        )
    return results


def validate_daily_panel(
    frame: pd.DataFrame,
    *,
    expected_source_row_count: int,
) -> list[ValidationResult]:
    dataset_name = "daily_panel"
    results = _missing_columns(
        frame,
        dataset_name=dataset_name,
        required_columns=set(DAILY_PANEL_CONTRACT.required_columns),
    )
    if results:
        return results
    results.extend(_validate_effective_unit_metadata(frame, dataset_name=dataset_name))
    if len(frame) != expected_source_row_count:
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.ERROR,
                check_name="observed_rows_preserved",
                message="Daily panel changed the selected source row count.",
                affected_row_count=abs(len(frame) - expected_source_row_count),
                blocks_output=True,
            )
        )
    null_keys = frame["symbol"].isna() | pd.to_datetime(frame["date"], errors="coerce").isna()
    if null_keys.any():
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.ERROR,
                check_name="panel_keys_not_null",
                message="Daily panel contains null symbol/date keys.",
                affected_columns=["symbol", "date"],
                affected_row_count=int(null_keys.sum()),
                blocks_output=True,
            )
        )
    duplicates = frame.duplicated(subset=["symbol", "date"])
    if duplicates.any():
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.ERROR,
                check_name="duplicate_symbol_date",
                message="Daily panel contains duplicate symbol/date keys.",
                affected_columns=["symbol", "date"],
                affected_row_count=int(duplicates.sum()),
                sample_affected_keys=_sample_keys(frame[duplicates], ["symbol", "date"]),
                blocks_output=True,
            )
        )
    results.extend(
        _numeric_non_negative(
            frame,
            dataset_name=dataset_name,
            columns=["open", "high", "low", "close", "volume"],
        )
    )
    volume = pd.to_numeric(frame["volume"], errors="coerce")
    non_integer_volume = volume.notna() & ((volume % 1) != 0)
    if non_integer_volume.any():
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.ERROR,
                check_name="volume_integer_like",
                message="Daily panel volume must be integer-like when present.",
                affected_columns=["volume"],
                affected_row_count=int(non_integer_volume.sum()),
                sample_affected_keys=_sample_keys(frame[non_integer_volume], ["symbol", "date"]),
                blocks_output=True,
            )
        )
    missing_ohlc = frame[["open", "high", "low", "close"]].isna().any(axis=1)
    if missing_ohlc.any():
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.WARNING,
                check_name="missing_ohlc_preserved",
                message="Observed source rows with missing OHLC remain present in the panel.",
                affected_columns=["open", "high", "low", "close"],
                affected_row_count=int(missing_ohlc.sum()),
                sample_affected_keys=_sample_keys(frame[missing_ohlc], ["symbol", "date"]),
            )
        )
    relationships = {
        "high_gte_low": frame["high"] < frame["low"],
        "high_gte_open_close": (frame["high"] < frame["open"]) | (frame["high"] < frame["close"]),
        "low_lte_open_close": (frame["low"] > frame["open"]) | (frame["low"] > frame["close"]),
    }
    for check_name, mask in relationships.items():
        if mask.any():
            results.append(
                _result(
                    dataset_name=dataset_name,
                    severity=ValidationSeverity.ERROR,
                    check_name=check_name,
                    message="Daily panel contains invalid OHLC relationships.",
                    affected_columns=["open", "high", "low", "close"],
                    affected_row_count=int(mask.sum()),
                    sample_affected_keys=_sample_keys(frame[mask], ["symbol", "date"]),
                    blocks_output=True,
                )
            )
    if (frame["observation_status"] != "observed_provider_bar").any():
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.ERROR,
                check_name="no_synthetic_bars",
                message="Daily panel contains rows not identified as observed provider bars.",
                affected_columns=["observation_status"],
                blocks_output=True,
            )
        )
    if (frame["price_adjustment_status"] == "unknown").any():
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.WARNING,
                check_name="price_adjustment_unknown",
                message="Adjusted versus unadjusted price semantics remain unresolved.",
                affected_columns=["price_adjustment_status"],
                affected_row_count=int((frame["price_adjustment_status"] == "unknown").sum()),
            )
        )
    sorted_keys = frame.sort_values(["symbol", "date"], kind="stable")[["symbol", "date"]]
    if (
        not frame[["symbol", "date"]]
        .reset_index(drop=True)
        .equals(sorted_keys.reset_index(drop=True))
    ):
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.ERROR,
                check_name="deterministic_ordering",
                message="Daily panel must be sorted by symbol/date.",
                affected_columns=["symbol", "date"],
                blocks_output=True,
            )
        )
    return results


def validate_liquidity_characterization(frame: pd.DataFrame) -> list[ValidationResult]:
    dataset_name = "liquidity"
    results = _missing_columns(
        frame,
        dataset_name=dataset_name,
        required_columns=set(LIQUIDITY_CONTRACT.required_columns),
    )
    if results:
        return results
    for column in ["trading_frequency", "zero_volume_frequency"]:
        numeric = pd.to_numeric(frame[column], errors="coerce")
        invalid = numeric.notna() & ~numeric.between(0, 1, inclusive="both")
        if invalid.any():
            results.append(
                _result(
                    dataset_name=dataset_name,
                    severity=ValidationSeverity.ERROR,
                    check_name=f"{column}_range",
                    message=f"{column} must be between zero and one.",
                    affected_columns=[column],
                    affected_row_count=int(invalid.sum()),
                    blocks_output=True,
                )
            )
    unverified = (
        frame["unit_verification_status"].astype("string") != UnitVerificationStatus.VERIFIED.value
    )
    provenance_unverified = (
        frame["unit_provenance_status"].astype("string")
        != UnitProvenanceStatus.VERIFIED.value
    )
    vnd_permitted = frame["vnd_traded_value_permitted"].fillna(False).astype(bool)
    monetary_value = pd.to_numeric(frame["average_traded_value_vnd"], errors="coerce").notna()
    false_monetary = (unverified | provenance_unverified | ~vnd_permitted) & monetary_value
    if false_monetary.any():
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.ERROR,
                check_name="monetary_units_verified",
                message="Unverified units cannot produce average_traded_value_vnd.",
                affected_columns=[
                    "unit_provenance_status",
                    "unit_verification_status",
                    "vnd_traded_value_permitted",
                    "average_traded_value_vnd",
                ],
                affected_row_count=int(false_monetary.sum()),
                blocks_output=True,
            )
        )
    invalid_permission = vnd_permitted & (unverified | provenance_unverified)
    if invalid_permission.any():
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.ERROR,
                check_name="vnd_permission_requires_verified_provenance",
                message="VND permission requires verified registered dataset provenance.",
                affected_columns=[
                    "unit_provenance_status",
                    "unit_verification_status",
                    "vnd_traded_value_permitted",
                ],
                affected_row_count=int(invalid_permission.sum()),
                blocks_output=True,
            )
        )
    unavailable_with_value = (
        frame["traded_value_unit"].astype("string") == TradedValueUnit.UNAVAILABLE.value
    ) & monetary_value
    if unavailable_with_value.any():
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.ERROR,
                check_name="traded_value_unit_consistency",
                message="Unavailable traded-value units cannot carry monetary values.",
                affected_columns=["traded_value_unit", "average_traded_value_vnd"],
                affected_row_count=int(unavailable_with_value.sum()),
                blocks_output=True,
            )
        )
    monetary_claim = (
        (~unverified)
        | vnd_permitted
        | monetary_value
        | (frame["traded_value_unit"].astype("string") == TradedValueUnit.VND.value)
    )
    if monetary_claim.any():
        results.extend(
            _validate_effective_unit_metadata(
                frame.loc[monetary_claim].copy(),
                dataset_name=dataset_name,
            )
        )
    return results


def _validate_effective_unit_metadata(
    frame: pd.DataFrame,
    *,
    dataset_name: str,
) -> list[ValidationResult]:
    policy = resolve_daily_unit_policy(frame)
    expected_metadata = effective_unit_metadata(policy)
    mismatch = pd.Series(False, index=frame.index)
    affected_columns: list[str] = []
    for column, expected in expected_metadata.items():
        if column not in frame.columns:
            continue
        if expected is None or bool(pd.isna(expected)):
            matches = frame[column].isna()
        elif isinstance(expected, bool):
            matches = frame[column].astype("boolean").eq(expected).fillna(False)
        else:
            matches = frame[column].astype("string").eq(str(expected)).fillna(False)
        column_mismatch = ~matches
        if column_mismatch.any():
            mismatch |= column_mismatch
            affected_columns.append(column)
    if not mismatch.any():
        return []
    return [
        _result(
            dataset_name=dataset_name,
            severity=ValidationSeverity.ERROR,
            check_name="effective_unit_provenance_consistency",
            message=(
                "Effective unit metadata does not match the provenance resolved from the "
                "dataset rows."
            ),
            affected_columns=affected_columns,
            affected_row_count=int(mismatch.sum()),
            sample_affected_keys=_sample_keys(frame[mismatch], ["symbol", "date"]),
            blocks_output=True,
        )
    ]


def validate_availability_diagnostics(frame: pd.DataFrame) -> list[ValidationResult]:
    dataset_name = "daily_availability"
    results = _missing_columns(
        frame,
        dataset_name=dataset_name,
        required_columns=set(AVAILABILITY_CONTRACT.required_columns),
    )
    if results:
        return results
    coverage = pd.to_numeric(frame["weekday_coverage_ratio"], errors="coerce")
    invalid = coverage.notna() & ~coverage.between(0, 1, inclusive="both")
    if invalid.any():
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.ERROR,
                check_name="weekday_coverage_ratio_range",
                message="Weekday coverage ratio must be between zero and one.",
                affected_columns=["weekday_coverage_ratio"],
                affected_row_count=int(invalid.sum()),
                blocks_output=True,
            )
        )
    absent_with_rows = frame["absence_of_data"].astype(bool) & (frame["observation_count"] > 0)
    if absent_with_rows.any():
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.ERROR,
                check_name="absence_consistency",
                message="Symbols marked absent cannot have observations.",
                affected_columns=["absence_of_data", "observation_count"],
                affected_row_count=int(absent_with_rows.sum()),
                blocks_output=True,
            )
        )
    return results


def validate_quote_snapshot(
    frame: pd.DataFrame,
    *,
    requested_symbols: list[str],
    missing_symbols: list[str] | None = None,
) -> list[ValidationResult]:
    dataset_name = "quotes"
    required = {
        "provider",
        "symbol",
        "snapshot_timestamp_utc",
        "provider_time_raw",
        "provider_time_parsed",
        "provider_time_parse_status",
        "exchange",
    }
    results = _missing_columns(frame, dataset_name=dataset_name, required_columns=required)
    if results:
        return results
    returned_symbols = {str(symbol) for symbol in frame["symbol"].dropna().tolist()}
    requested = {symbol.upper() for symbol in requested_symbols}
    missing = sorted((requested - returned_symbols) | set(missing_symbols or []))
    if missing:
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.WARNING,
                check_name="missing_requested_symbols",
                message=f"Provider did not return quote rows for: {', '.join(missing)}.",
                affected_columns=["symbol"],
                affected_row_count=len(missing),
                sample_affected_keys=missing[:5],
            )
        )
    if frame["provider_time_raw"].isna().any():
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.WARNING,
                check_name="provider_time_raw_present",
                message="Quote rows are missing provider_time_raw.",
                affected_columns=["provider_time_raw"],
                affected_row_count=int(frame["provider_time_raw"].isna().sum()),
            )
        )
    parsed = pd.to_datetime(frame["provider_time_parsed"], errors="coerce")
    misleading = parsed.notna() & (parsed.dt.year < 2000)
    if misleading.any():
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.ERROR,
                check_name="provider_time_not_misleading_1970",
                message="Quote provider_time_parsed contains misleading pre-2000 timestamps.",
                affected_columns=["provider_time_parsed", "provider_time_parse_status"],
                affected_row_count=int(misleading.sum()),
                sample_affected_keys=_sample_keys(frame[misleading], ["symbol"]),
                blocks_output=True,
            )
        )
    numeric_raw = (
        frame["provider_time_raw"].astype("string").str.fullmatch(r"\d+(\.\d+)?").fillna(False)
    )
    status = frame["provider_time_parse_status"].astype("string")
    bad_numeric_status = numeric_raw & (
        status != ProviderTimeParseStatus.PROVIDER_SPECIFIC_UNPARSED.value
    )
    if bad_numeric_status.any():
        results.append(
            _result(
                dataset_name=dataset_name,
                severity=ValidationSeverity.ERROR,
                check_name="numeric_provider_time_unparsed",
                message="Numeric quote provider_time_raw must remain provider-specific/unparsed.",
                affected_columns=["provider_time_raw", "provider_time_parse_status"],
                affected_row_count=int(bad_numeric_status.sum()),
                sample_affected_keys=_sample_keys(frame[bad_numeric_status], ["symbol"]),
                blocks_output=True,
            )
        )
    price_columns = [
        column
        for column in frame.columns
        if column.endswith("_price") or column in {"price_change", "percent_change"}
    ]
    volume_columns = [
        column
        for column in frame.columns
        if "vol" in column or column in {"total_value", "foreign_room"}
    ]
    results.extend(_numeric_non_negative(frame, dataset_name=dataset_name, columns=volume_columns))
    for column in price_columns:
        numeric = pd.to_numeric(frame[column], errors="coerce")
        if numeric.isna().any() and frame[column].notna().any():
            results.append(
                _result(
                    dataset_name=dataset_name,
                    severity=ValidationSeverity.WARNING,
                    check_name=f"{column}_numeric",
                    message=f"{column} contains non-numeric provider values.",
                    affected_columns=[column],
                    affected_row_count=int((numeric.isna() & frame[column].notna()).sum()),
                )
            )
    return results


def render_validation_markdown(results: list[ValidationResult]) -> str:
    lines = ["# Data Quality Report", ""]
    if not results:
        lines.extend(["No validation results were produced.", ""])
        return "\n".join(lines)
    for result in results:
        lines.extend(
            [
                f"## {result.dataset_name}: {result.check_name}",
                "",
                f"- Severity: {result.severity.value}",
                f"- Blocks output: {result.blocks_output}",
                f"- Message: {result.message}",
                f"- Affected columns: {', '.join(result.affected_columns) or 'none'}",
                f"- Affected row count: {result.affected_row_count}",
                f"- Sample keys: {', '.join(result.sample_affected_keys) or 'none'}",
                "",
            ]
        )
    return "\n".join(lines)


def write_validation_reports(
    results: list[ValidationResult],
    *,
    json_path: Path,
    markdown_path: Path,
) -> tuple[Path, Path]:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "summary": summarize_validation(results),
        "results": [result.model_dump(mode="json") for result in results],
    }
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_validation_markdown(results), encoding="utf-8")
    return json_path, markdown_path
