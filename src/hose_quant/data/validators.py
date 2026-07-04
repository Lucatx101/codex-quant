from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from hose_quant.data.models import (
    ProviderTimeParseStatus,
    UniverseDiagnostics,
    ValidationResult,
    ValidationSeverity,
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
