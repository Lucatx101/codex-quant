from __future__ import annotations

from datetime import UTC, datetime
from numbers import Number
from typing import Any

import pandas as pd

from hose_quant.data.market_time import timestamp_provenance
from hose_quant.data.models import (
    BarStatus,
    DailyUnitProvenance,
    ProviderTimeParseStatus,
    UniverseDiagnostics,
    VolumeSemantics,
)
from hose_quant.data.unit_provenance import daily_provenance_columns

PROVIDER = "vnstock"
QUOTE_PRICE_COLUMNS = [
    "reference_price",
    "ceiling_price",
    "floor_price",
    "open_price",
    "high_price",
    "low_price",
    "close_price",
    "average_price",
    "price_change",
    "percent_change",
    "bid_price_1",
    "bid_price_2",
    "bid_price_3",
    "ask_price_1",
    "ask_price_2",
    "ask_price_3",
]
QUOTE_VOLUME_COLUMNS = [
    "volume_accumulated",
    "total_value",
    "bid_vol_1",
    "bid_vol_2",
    "bid_vol_3",
    "ask_vol_1",
    "ask_vol_2",
    "ask_vol_3",
    "foreign_buy_volume",
    "foreign_sell_volume",
    "foreign_room",
]


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _column(frame: pd.DataFrame, name: str, default: Any = pd.NA) -> pd.Series:
    if name in frame.columns:
        return frame[name]
    return pd.Series([default] * len(frame), index=frame.index)


def _normalized_text(value: Any) -> Any:
    if pd.isna(value):
        return pd.NA
    text = str(value).strip()
    return text.upper() if text else pd.NA


def _as_numeric(frame: pd.DataFrame, columns: list[str]) -> None:
    for column in columns:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")


def normalize_universe_snapshot(
    raw: pd.DataFrame,
    *,
    exchange: str,
    snapshot_timestamp_utc: datetime | None = None,
) -> tuple[pd.DataFrame, UniverseDiagnostics]:
    snapshot_timestamp_utc = snapshot_timestamp_utc or utc_now()
    frame = raw.copy()
    normalized_exchange = exchange.upper()
    raw_exchange = _column(frame, "exchange")
    raw_type = _column(frame, "type")
    normalized = pd.DataFrame(
        {
            "provider": PROVIDER,
            "exchange": raw_exchange.map(_normalized_text),
            "symbol": _column(frame, "symbol").map(_normalized_text),
            "organ_name": _column(frame, "organ_name"),
            "english_organ_name": _column(frame, "en_organ_name"),
            "security_type": raw_type.fillna("UNKNOWN").astype("string"),
            "provider_id": _column(frame, "id"),
            "snapshot_timestamp_utc": snapshot_timestamp_utc,
            "raw_exchange_field": raw_exchange,
            "raw_type_field": raw_type,
        }
    )

    rows_by_type = {
        str(key): int(value)
        for key, value in raw_type.fillna("UNKNOWN").astype(str).value_counts().to_dict().items()
    }
    diagnostics = UniverseDiagnostics(
        total_returned_rows=len(normalized),
        hose_rows=int((normalized["exchange"] == "HOSE").sum()),
        null_exchange_rows=int(normalized["exchange"].isna().sum()),
        rows_by_security_type=rows_by_type,
        duplicate_symbols=int(normalized["symbol"].duplicated().sum()),
    )
    filtered = normalized[normalized["exchange"] == normalized_exchange].copy()
    return filtered.sort_values(["symbol"]).reset_index(drop=True), diagnostics


def normalize_daily_ohlcv(
    raw: pd.DataFrame,
    *,
    symbol: str,
    exchange: str | None = None,
    ingestion_timestamp_utc: datetime | None = None,
    unit_provenance: DailyUnitProvenance | None = None,
) -> pd.DataFrame:
    if unit_provenance is not None and (
        unit_provenance.provider != PROVIDER or unit_provenance.source_resolution != "1D"
    ):
        raise ValueError(
            "Daily unit provenance must match the vnstock provider and 1D source resolution."
        )
    ingestion_timestamp_utc = ingestion_timestamp_utc or utc_now()
    frame = raw.copy()
    parsed = pd.to_datetime(_column(frame, "time"), errors="coerce")
    normalized = pd.DataFrame(
        {
            "provider": PROVIDER,
            "symbol": symbol.upper(),
            "exchange": exchange.upper() if exchange else pd.NA,
            "date": parsed.dt.date,
            "open": _column(frame, "open"),
            "high": _column(frame, "high"),
            "low": _column(frame, "low"),
            "close": _column(frame, "close"),
            "volume": _column(frame, "volume"),
            "adjusted_flag": pd.NA,
            "source_resolution": "1D",
            "ingestion_timestamp_utc": ingestion_timestamp_utc,
        }
    )
    for column, value in daily_provenance_columns(unit_provenance).items():
        normalized[column] = value
    _as_numeric(normalized, ["open", "high", "low", "close", "volume"])
    return normalized.sort_values(["symbol", "date"]).reset_index(drop=True)


def normalize_intraday_bars(
    raw: pd.DataFrame,
    *,
    symbol: str,
    resolution: str,
    exchange: str | None = None,
    ingestion_timestamp_utc: datetime | None = None,
) -> pd.DataFrame:
    ingestion_timestamp_utc = ingestion_timestamp_utc or utc_now()
    frame = raw.copy()
    provider_times = _column(frame, "time")
    time_provenance = [timestamp_provenance(value, provider=PROVIDER) for value in provider_times]
    parsed = pd.Series(
        [item.parsed_value for item in time_provenance],
        index=frame.index,
    )
    parsed = pd.to_datetime(parsed, errors="coerce")
    normalized = pd.DataFrame(
        {
            "provider": PROVIDER,
            "symbol": symbol.upper(),
            "exchange": exchange.upper() if exchange else pd.NA,
            "provider_timestamp_raw": [item.original_value for item in time_provenance],
            "timestamp": parsed,
            "trading_date": parsed.dt.date,
            "timestamp_timezone_status": [item.awareness_status.value for item in time_provenance],
            "timestamp_interpretation": [item.interpretation for item in time_provenance],
            "timestamp_localization_applied": [
                item.localization_applied for item in time_provenance
            ],
            "resolution": resolution,
            "open": _column(frame, "open"),
            "high": _column(frame, "high"),
            "low": _column(frame, "low"),
            "close": _column(frame, "close"),
            "volume": _column(frame, "volume"),
            "volume_semantics": VolumeSemantics.UNKNOWN.value,
            "bar_status": BarStatus.UNKNOWN.value,
            "ingestion_timestamp_utc": ingestion_timestamp_utc,
        }
    )
    _as_numeric(normalized, ["open", "high", "low", "close", "volume"])
    return normalized.sort_values(["symbol", "resolution", "timestamp"]).reset_index(drop=True)


def parse_provider_time(value: Any) -> tuple[Any, pd.Timestamp | None, ProviderTimeParseStatus]:
    if pd.isna(value):
        return pd.NA, None, ProviderTimeParseStatus.MISSING
    raw = str(value)
    if isinstance(value, Number) and not isinstance(value, bool):
        return raw, None, ProviderTimeParseStatus.PROVIDER_SPECIFIC_UNPARSED
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(parsed):
        return raw, None, ProviderTimeParseStatus.INVALID
    timestamp = pd.Timestamp(parsed)
    if timestamp.year < 2000:
        return raw, None, ProviderTimeParseStatus.INVALID
    return raw, timestamp, ProviderTimeParseStatus.PARSED


def normalize_quote_snapshot(
    raw: pd.DataFrame,
    *,
    requested_symbols: list[str],
    snapshot_timestamp_utc: datetime | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    snapshot_timestamp_utc = snapshot_timestamp_utc or utc_now()
    frame = raw.copy()
    frame["symbol"] = _column(frame, "symbol").map(_normalized_text)
    parsed_times = list(_column(frame, "time").map(parse_provider_time))
    provider_time_raw = [item[0] for item in parsed_times]
    provider_time_parsed = [item[1] for item in parsed_times]
    provider_time_status = [item[2].value for item in parsed_times]

    normalized = pd.DataFrame(
        {
            "provider": PROVIDER,
            "symbol": frame["symbol"],
            "snapshot_timestamp_utc": snapshot_timestamp_utc,
            "provider_time_raw": provider_time_raw,
            "provider_time_parsed": provider_time_parsed,
            "provider_time_parse_status": provider_time_status,
            "exchange": _column(frame, "exchange").map(_normalized_text),
        }
    )
    for column in QUOTE_PRICE_COLUMNS + QUOTE_VOLUME_COLUMNS:
        normalized[column] = _column(frame, column)
    _as_numeric(normalized, QUOTE_PRICE_COLUMNS + QUOTE_VOLUME_COLUMNS)

    returned = {str(symbol) for symbol in normalized["symbol"].dropna().tolist()}
    requested = {symbol.upper() for symbol in requested_symbols}
    missing = sorted(requested - returned)
    return normalized.sort_values(["symbol"]).reset_index(drop=True), missing
