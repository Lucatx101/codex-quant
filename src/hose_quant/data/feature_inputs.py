from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from hose_quant.data.contracts import (
    AVAILABILITY_CONTRACT_VERSION,
    DAILY_PANEL_CONTRACT_VERSION,
    LIQUIDITY_CONTRACT_VERSION,
    UNIVERSE_CONTRACT_VERSION,
)
from hose_quant.data.market_time import (
    HOLIDAY_CALENDAR_STATUS,
    TARGET_MARKET_TIMEZONE,
    aware_timestamp_to_utc,
    timestamp_provenance,
)
from hose_quant.data.models import (
    HistoricalMembershipStatus,
    LiquidityScreenConfig,
    LiquidityScreenStatus,
    MissingDataStatus,
    PriceAdjustmentStatus,
    UniverseCandidateStatus,
)
from hose_quant.data.unit_provenance import (
    effective_unit_metadata,
    ensure_daily_provenance_columns,
    resolve_daily_unit_policy,
    unit_provenance_output_metadata,
)

SYMBOL_PATTERN = r"[A-Z0-9]{1,16}"
SUPPORTED_STOCK_TYPES = {"stock", "equity"}
KNOWN_NON_STOCK_TYPES = {
    "bond",
    "cw",
    "etf",
    "fund",
    "futures",
    "warrant",
}


class UnverifiedLiquidityUnitsError(ValueError):
    """Raised when a monetary metric is requested without verified units."""


def prepare_research_universe(
    frame: pd.DataFrame,
    *,
    exchange: str,
    requested_reference_date: date | None = None,
) -> pd.DataFrame:
    required = {
        "provider",
        "exchange",
        "symbol",
        "security_type",
        "snapshot_timestamp_utc",
    }
    _require_columns(frame, required, dataset="normalized universe")
    normalized_exchange = exchange.strip().upper()
    output = frame.copy().reset_index(drop=True)
    output["input_row_number"] = output.index.astype("int64")
    output["symbol_raw"] = output["symbol"].astype("string")
    output["symbol"] = output["symbol"].map(_normalize_symbol).astype("string")
    output["exchange"] = output["exchange"].astype("string").str.strip().str.upper()
    output["provider_security_type"] = output["security_type"].astype("string")
    output["classification_provenance"] = "provider_reported_unverified"
    snapshot_provenance = [
        timestamp_provenance(value, provider=str(provider))
        for value, provider in zip(
            output["snapshot_timestamp_utc"], output["provider"], strict=True
        )
    ]
    output["source_snapshot_timestamp_raw"] = [item.original_value for item in snapshot_provenance]
    output["source_snapshot_observed_at_utc"] = pd.to_datetime(
        [
            aware_timestamp_to_utc(value, provider=str(provider))
            for value, provider in zip(
                output["snapshot_timestamp_utc"], output["provider"], strict=True
            )
        ],
        errors="coerce",
        utc=True,
    )
    output["source_snapshot_timezone_status"] = [
        item.awareness_status.value for item in snapshot_provenance
    ]
    output["source_snapshot_interpretation"] = [item.interpretation for item in snapshot_provenance]
    output["source_snapshot_localization_applied"] = [
        item.localization_applied for item in snapshot_provenance
    ]
    output["requested_reference_date"] = (
        pd.Timestamp(requested_reference_date) if requested_reference_date else pd.NaT
    )
    snapshot_dates = output["source_snapshot_observed_at_utc"].dt.date
    historical_reference = requested_reference_date is not None and bool(
        (snapshot_dates != requested_reference_date).fillna(True).any()
    )
    membership_status = (
        HistoricalMembershipStatus.REQUESTED_REFERENCE_UNVERIFIED
        if historical_reference
        else HistoricalMembershipStatus.CURRENT_SNAPSHOT_ONLY
    )
    output["historical_membership_status"] = membership_status.value
    output["historical_membership_verified"] = False
    output["tradability_status"] = "not_verified"

    duplicate_symbols = output["symbol"].notna() & output["symbol"].duplicated(keep=False)
    candidate_statuses: list[str] = []
    candidate_reasons: list[str] = []
    for index, row in output.iterrows():
        reasons: list[str] = []
        symbol = row["symbol"]
        security_type = str(row["provider_security_type"]).strip().lower()
        row_exchange = row["exchange"]

        if pd.isna(symbol) or not bool(pd.Series([symbol]).str.fullmatch(SYMBOL_PATTERN).iloc[0]):
            status = UniverseCandidateStatus.EXCLUDED
            reasons.append("malformed_or_missing_symbol")
        elif pd.isna(row_exchange) or row_exchange != normalized_exchange:
            status = UniverseCandidateStatus.EXCLUDED
            reasons.append("exchange_mismatch_or_missing")
        elif bool(duplicate_symbols.iloc[index]):
            status = UniverseCandidateStatus.UNCERTAIN
            reasons.append("duplicate_symbol_records")
        elif security_type in SUPPORTED_STOCK_TYPES:
            status = UniverseCandidateStatus.INCLUDED_CANDIDATE
            reasons.append("provider_reported_stock_candidate")
        elif security_type in KNOWN_NON_STOCK_TYPES:
            status = UniverseCandidateStatus.EXCLUDED
            reasons.append(f"provider_reported_non_stock_type:{security_type}")
        else:
            status = UniverseCandidateStatus.UNCERTAIN
            reasons.append("unsupported_or_unknown_security_type")

        if historical_reference:
            reasons.append("historical_membership_not_verified")
        candidate_statuses.append(status.value)
        candidate_reasons.append(json.dumps(reasons, separators=(",", ":")))

    output["candidate_status"] = candidate_statuses
    output["candidate_reasons"] = candidate_reasons
    output["feature_input_contract_version"] = UNIVERSE_CONTRACT_VERSION
    output = output.drop(columns=["security_type", "snapshot_timestamp_utc"], errors="ignore")
    first_columns = [
        "feature_input_contract_version",
        "input_row_number",
        "provider",
        "exchange",
        "symbol_raw",
        "symbol",
        "provider_security_type",
        "classification_provenance",
        "candidate_status",
        "candidate_reasons",
        "tradability_status",
        "source_snapshot_timestamp_raw",
        "source_snapshot_observed_at_utc",
        "source_snapshot_timezone_status",
        "source_snapshot_interpretation",
        "source_snapshot_localization_applied",
        "requested_reference_date",
        "historical_membership_status",
        "historical_membership_verified",
    ]
    ordered = output[
        first_columns + [column for column in output.columns if column not in first_columns]
    ]
    return ordered.sort_values(
        ["candidate_status", "symbol", "input_row_number"],
        kind="stable",
        na_position="last",
    ).reset_index(drop=True)


def apply_liquidity_to_universe(
    universe: pd.DataFrame,
    liquidity: pd.DataFrame,
) -> pd.DataFrame:
    if liquidity.duplicated(subset=["symbol"]).any():
        raise ValueError("Liquidity characterization must contain one row per symbol.")
    liquidity_columns = [
        column for column in liquidity.columns if column not in {"feature_input_contract_version"}
    ]
    renamed = liquidity[liquidity_columns].rename(
        columns={
            column: f"liquidity_{column}" for column in liquidity_columns if column != "symbol"
        }
    )
    output = universe.merge(renamed, how="left", on="symbol", validate="many_to_one")
    for index, row in output.iterrows():
        if row["candidate_status"] != UniverseCandidateStatus.INCLUDED_CANDIDATE.value:
            continue
        screen_status = row.get("liquidity_screen_status")
        reasons = json.loads(str(row["candidate_reasons"]))
        if screen_status == LiquidityScreenStatus.FAILED.value:
            output.at[index, "candidate_status"] = UniverseCandidateStatus.EXCLUDED.value
            reasons.append("liquidity_screen_failed")
        elif screen_status in {
            LiquidityScreenStatus.INSUFFICIENT_HISTORY.value,
            LiquidityScreenStatus.ABSENT_DATA.value,
        }:
            output.at[index, "candidate_status"] = UniverseCandidateStatus.UNCERTAIN.value
            reasons.append(f"liquidity_{screen_status}")
        elif screen_status == LiquidityScreenStatus.PASSED.value:
            reasons.append("liquidity_screen_passed")
        else:
            output.at[index, "candidate_status"] = UniverseCandidateStatus.UNCERTAIN.value
            reasons.append("liquidity_not_available")
        output.at[index, "candidate_reasons"] = json.dumps(reasons, separators=(",", ":"))
    return output.sort_values(
        ["candidate_status", "symbol", "input_row_number"], kind="stable", na_position="last"
    ).reset_index(drop=True)


def build_daily_panel(
    frame: pd.DataFrame,
    *,
    symbols: list[str] | None,
    start: date,
    end: date,
) -> pd.DataFrame:
    if start > end:
        raise ValueError("Daily panel start date must not be after end date.")
    required = {
        "provider",
        "symbol",
        "exchange",
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
    _require_columns(frame, required, dataset="normalized daily")
    output = ensure_daily_provenance_columns(frame)
    output["symbol"] = output["symbol"].map(_normalize_symbol).astype("string")
    output["date"] = pd.to_datetime(output["date"], errors="coerce").dt.normalize()
    selected_symbols = _clean_symbol_list(symbols or [])
    if selected_symbols:
        output = output[output["symbol"].isin(selected_symbols)]
    start_timestamp = pd.Timestamp(start)
    end_timestamp = pd.Timestamp(end)
    output = output[output["date"].between(start_timestamp, end_timestamp, inclusive="both")]
    output = output.copy()
    unit_policy = resolve_daily_unit_policy(output)

    for column in ["open", "high", "low", "close", "volume"]:
        output[column] = pd.to_numeric(output[column], errors="coerce")
    output["source_adjusted_flag"] = output["adjusted_flag"]
    output["price_adjustment_status"] = output["adjusted_flag"].map(
        lambda value: (
            PriceAdjustmentStatus.UNKNOWN.value
            if pd.isna(value)
            else PriceAdjustmentStatus.PROVIDER_FLAG_UNVERIFIED.value
        )
    )
    output["source_ingestion_timestamp_utc"] = pd.to_datetime(
        output["ingestion_timestamp_utc"], errors="coerce", utc=True
    )
    output["observation_status"] = "observed_provider_bar"
    output["source_dataset"] = "normalized/vnstock/daily"
    output["daily_date_semantics"] = "provider_trading_date_unlocalized"
    output["timestamp_status"] = "daily_date_no_intraday_timestamp"
    output["market_timezone_convention"] = TARGET_MARKET_TIMEZONE
    for column, value in effective_unit_metadata(unit_policy).items():
        output[column] = value
    output["feature_input_contract_version"] = DAILY_PANEL_CONTRACT_VERSION
    output = output.drop(columns=["adjusted_flag", "ingestion_timestamp_utc"], errors="ignore")

    columns = [
        "feature_input_contract_version",
        "provider",
        "symbol",
        "exchange",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "observation_status",
        "price_adjustment_status",
        "source_adjusted_flag",
        "source_resolution",
        "data_backend",
        "unit_provenance_schema_version",
        "source_unit_policy_name",
        "source_unit_policy_version",
        "source_price_unit",
        "source_volume_unit",
        "source_price_scale_to_vnd",
        "source_volume_scale_to_shares",
        "source_unit_evidence_reference",
        "source_ingestion_timestamp_utc",
        "source_dataset",
        "daily_date_semantics",
        "timestamp_status",
        "market_timezone_convention",
        "unit_provenance_status",
        "unit_verification_status",
        "unit_policy_name",
        "unit_policy_version",
        "price_unit",
        "volume_unit",
        "traded_value_unit",
        "unit_evidence_reference",
        "unit_verification_reason",
        "vnd_traded_value_permitted",
    ]
    return output[columns].sort_values(["symbol", "date"], kind="stable").reset_index(drop=True)


def characterize_liquidity(
    daily: pd.DataFrame,
    *,
    symbols: list[str],
    reference_date: date,
    config: LiquidityScreenConfig,
) -> pd.DataFrame:
    required = {"symbol", "date", "close", "volume"}
    _require_columns(daily, required, dataset="daily panel")
    clean_symbols = _clean_symbol_list(symbols)
    if not clean_symbols:
        raise ValueError("At least one symbol is required for liquidity characterization.")
    working = daily.copy()
    working["symbol"] = working["symbol"].map(_normalize_symbol).astype("string")
    working["date"] = pd.to_datetime(working["date"], errors="coerce").dt.normalize()
    working["close"] = pd.to_numeric(working["close"], errors="coerce")
    working["volume"] = pd.to_numeric(working["volume"], errors="coerce")
    relevant = working[working["symbol"].isin(clean_symbols)].copy()
    if relevant.duplicated(subset=["symbol", "date"]).any():
        raise ValueError("Liquidity input contains duplicate symbol/date keys.")
    if (relevant["volume"].dropna() < 0).any():
        raise ValueError("Liquidity input contains negative volume.")

    expected_weekdays = pd.bdate_range(
        end=pd.Timestamp(reference_date), periods=config.window_weekdays
    )
    window_start = expected_weekdays[0]
    reference_timestamp = pd.Timestamp(reference_date)
    relevant = relevant[
        relevant["date"].between(window_start, reference_timestamp, inclusive="both")
    ]
    unit_policy = resolve_daily_unit_policy(relevant)
    if config.min_average_traded_value_vnd is not None and not unit_policy.can_compute_vnd:
        raise UnverifiedLiquidityUnitsError(
            "A VND liquidity threshold requires matching machine-checkable unit provenance in "
            f"every selected daily row. Effective provenance status is "
            f"{unit_policy.provenance_status.value}: {unit_policy.verification_reason}"
        )
    expected_set = set(expected_weekdays)
    provenance_metadata = unit_provenance_output_metadata(unit_policy)

    rows: list[dict[str, Any]] = []
    for symbol in clean_symbols:
        all_window_rows = relevant[relevant["symbol"] == symbol].sort_values("date", kind="stable")
        out_of_calendar_count = int((~all_window_rows["date"].isin(expected_set)).sum())
        group = all_window_rows[all_window_rows["date"].isin(expected_set)]
        observed_date_count = int(group["date"].nunique())
        volume = group["volume"]
        valid_volume_count = int(volume.notna().sum())
        positive_volume_count = int((volume > 0).sum())
        zero_volume_count = int((volume == 0).sum())
        trading_frequency = positive_volume_count / config.window_weekdays
        zero_volume_frequency = (
            zero_volume_count / valid_volume_count if valid_volume_count else None
        )
        average_volume = float(volume.mean()) if valid_volume_count else None
        valid_close = group.dropna(subset=["close"])
        recent_close = float(valid_close.iloc[-1]["close"]) if not valid_close.empty else None
        recent_close_date = valid_close.iloc[-1]["date"] if not valid_close.empty else pd.NaT
        missing_status = _missing_data_status(observed_date_count, config.window_weekdays)
        insufficient_history = observed_date_count < config.min_history_observations

        average_traded_value_vnd: float | None = None
        if unit_policy.can_compute_vnd:
            valid_value = group.dropna(subset=["close", "volume"])
            if not valid_value.empty:
                traded_value = (
                    valid_value["close"]
                    * float(unit_policy.price_scale_to_vnd or 0)
                    * valid_value["volume"]
                    * float(unit_policy.volume_scale_to_shares or 0)
                )
                average_traded_value_vnd = float(traded_value.mean())

        screen_status, reasons = _screen_liquidity(
            observed_date_count=observed_date_count,
            insufficient_history=insufficient_history,
            trading_frequency=trading_frequency,
            zero_volume_frequency=zero_volume_frequency,
            average_volume=average_volume,
            average_traded_value_vnd=average_traded_value_vnd,
            config=config,
        )
        if out_of_calendar_count:
            screen_status = LiquidityScreenStatus.FAILED
            reasons.append("out_of_weekday_calendar_observations")
        rows.append(
            {
                "feature_input_contract_version": LIQUIDITY_CONTRACT_VERSION,
                "symbol": symbol,
                "reference_date": reference_timestamp,
                "window_start_date": window_start,
                "window_weekdays": config.window_weekdays,
                "observed_date_count": observed_date_count,
                "valid_volume_observation_count": valid_volume_count,
                "positive_volume_observation_count": positive_volume_count,
                "zero_volume_observation_count": zero_volume_count,
                "out_of_weekday_calendar_observation_count": out_of_calendar_count,
                "trading_frequency": trading_frequency,
                "zero_volume_frequency": zero_volume_frequency,
                "average_volume_provider_units": average_volume,
                "average_traded_value_vnd": average_traded_value_vnd,
                "recent_valid_close": recent_close,
                "recent_valid_close_date": recent_close_date,
                "insufficient_history": insufficient_history,
                "missing_data_status": missing_status.value,
                "screen_status": screen_status.value,
                "screen_reasons": json.dumps(reasons, separators=(",", ":")),
                **provenance_metadata,
            }
        )
    return pd.DataFrame(rows).sort_values("symbol", kind="stable").reset_index(drop=True)


def daily_availability_diagnostics(
    daily: pd.DataFrame,
    *,
    symbols: list[str],
    start: date,
    end: date,
) -> pd.DataFrame:
    if start > end:
        raise ValueError("Diagnostics start date must not be after end date.")
    required = {"symbol", "date", "open", "high", "low", "close", "volume"}
    _require_columns(daily, required, dataset="daily observations")
    clean_symbols = _clean_symbol_list(symbols)
    working = daily.copy()
    working["symbol"] = working["symbol"].map(_normalize_symbol).astype("string")
    working["date"] = pd.to_datetime(working["date"], errors="coerce").dt.normalize()
    for column in ["open", "high", "low", "close", "volume"]:
        working[column] = pd.to_numeric(working[column], errors="coerce")
    start_timestamp = pd.Timestamp(start)
    end_timestamp = pd.Timestamp(end)
    working = working[working["date"].between(start_timestamp, end_timestamp, inclusive="both")]
    expected = pd.bdate_range(start=start_timestamp, end=end_timestamp)
    expected_set = set(expected)

    rows: list[dict[str, Any]] = []
    for symbol in clean_symbols:
        group = working[working["symbol"] == symbol].copy()
        valid_dates = group["date"].dropna()
        observed_dates = set(valid_dates)
        observed_expected = observed_dates & expected_set
        missing_ohlc = group[["open", "high", "low", "close"]].isna().any(axis=1)
        invalid_ohlc = (
            (group["high"] < group["low"])
            | (group["high"] < group["open"])
            | (group["high"] < group["close"])
            | (group["low"] > group["open"])
            | (group["low"] > group["close"])
            | (group[["open", "high", "low", "close"]] < 0).any(axis=1)
        )
        expected_count = len(expected)
        observed_expected_count = len(observed_expected)
        rows.append(
            {
                "feature_input_contract_version": AVAILABILITY_CONTRACT_VERSION,
                "symbol": symbol,
                "requested_start_date": start_timestamp,
                "requested_end_date": end_timestamp,
                "observed_start_date": valid_dates.min() if not valid_dates.empty else pd.NaT,
                "observed_end_date": valid_dates.max() if not valid_dates.empty else pd.NaT,
                "observation_count": len(group),
                "duplicate_count": int(group.duplicated(subset=["symbol", "date"]).sum()),
                "missing_ohlc_count": int(missing_ohlc.sum()),
                "invalid_ohlc_count": int(invalid_ohlc.sum()),
                "zero_volume_count": int((group["volume"] == 0).sum()),
                "expected_weekday_count": expected_count,
                "observed_expected_weekday_count": observed_expected_count,
                "missing_expected_weekday_count": expected_count - observed_expected_count,
                "weekday_coverage_ratio": (
                    observed_expected_count / expected_count if expected_count else None
                ),
                "weekend_observation_count": int((valid_dates.dt.dayofweek >= 5).sum()),
                "absence_of_data": group.empty,
                "expected_session_model": "weekdays_only",
                "holiday_calendar_status": HOLIDAY_CALENDAR_STATUS,
            }
        )
    return pd.DataFrame(rows).sort_values("symbol", kind="stable").reset_index(drop=True)


def write_availability_report(
    diagnostics: pd.DataFrame,
    *,
    json_path: Path,
    markdown_path: Path,
) -> tuple[Path, Path]:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    rows = json.loads(diagnostics.to_json(orient="records", date_format="iso"))
    payload = {
        "contract_version": AVAILABILITY_CONTRACT_VERSION,
        "expected_session_model": "weekdays_only",
        "holiday_calendar_status": HOLIDAY_CALENDAR_STATUS,
        "rows": rows,
    }
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "# Daily Availability Diagnostics",
        "",
        "Expected sessions use weekdays only. Vietnamese public holidays, exchange closures,",
        "and symbol-specific halts are not removed from the missing-weekday count.",
        "",
        "| Symbol | Observations | Duplicates | Invalid OHLC | Zero volume | Weekday coverage |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in diagnostics.itertuples(index=False):
        coverage = (
            "n/a" if pd.isna(row.weekday_coverage_ratio) else f"{row.weekday_coverage_ratio:.3f}"
        )
        lines.append(
            f"| {row.symbol} | {row.observation_count} | {row.duplicate_count} | "
            f"{row.invalid_ohlc_count} | {row.zero_volume_count} | {coverage} |"
        )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def _normalize_symbol(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip().upper()
    return text or None


def _clean_symbol_list(values: list[str]) -> list[str]:
    cleaned = {_normalize_symbol(value) for value in values}
    return sorted(symbol for symbol in cleaned if symbol is not None)


def _require_columns(frame: pd.DataFrame, required: set[str], *, dataset: str) -> None:
    missing = sorted(required - set(map(str, frame.columns)))
    if missing:
        raise ValueError(f"{dataset} is missing required columns: {', '.join(missing)}.")


def _missing_data_status(observed: int, expected: int) -> MissingDataStatus:
    if observed == 0:
        return MissingDataStatus.ABSENT
    if observed >= expected:
        return MissingDataStatus.COMPLETE_WEEKDAY_COVERAGE
    return MissingDataStatus.PARTIAL


def _screen_liquidity(
    *,
    observed_date_count: int,
    insufficient_history: bool,
    trading_frequency: float,
    zero_volume_frequency: float | None,
    average_volume: float | None,
    average_traded_value_vnd: float | None,
    config: LiquidityScreenConfig,
) -> tuple[LiquidityScreenStatus, list[str]]:
    if observed_date_count == 0:
        return LiquidityScreenStatus.ABSENT_DATA, ["no_daily_observations"]
    if insufficient_history:
        return LiquidityScreenStatus.INSUFFICIENT_HISTORY, ["insufficient_history"]

    reasons: list[str] = []
    if (
        config.min_trading_frequency is not None
        and trading_frequency < config.min_trading_frequency
    ):
        reasons.append("trading_frequency_below_minimum")
    if config.max_zero_volume_frequency is not None and (
        zero_volume_frequency is None or zero_volume_frequency > config.max_zero_volume_frequency
    ):
        reasons.append("zero_volume_frequency_above_maximum")
    if config.min_average_volume_provider_units is not None and (
        average_volume is None or average_volume < config.min_average_volume_provider_units
    ):
        reasons.append("average_volume_below_minimum")
    if config.min_average_traded_value_vnd is not None and (
        average_traded_value_vnd is None
        or average_traded_value_vnd < config.min_average_traded_value_vnd
    ):
        reasons.append("average_traded_value_vnd_below_minimum")
    if reasons:
        return LiquidityScreenStatus.FAILED, reasons
    return LiquidityScreenStatus.PASSED, ["configured_liquidity_criteria_passed"]
