from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from hose_quant.data.models import TimestampAwarenessStatus, TimestampProvenance

MARKET_TIME_POLICY_VERSION = "hose-market-time-v1"
TARGET_MARKET_TIMEZONE = "Asia/Ho_Chi_Minh"
HOLIDAY_CALENDAR_STATUS = "weekday_only_vietnam_holidays_not_applied"


class SessionWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    start_local: str
    end_local: str
    matching_method: str


class MarketTimePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: str = MARKET_TIME_POLICY_VERSION
    target_market_timezone: str = TARGET_MARKET_TIMEZONE
    weekdays: list[str] = Field(
        default_factory=lambda: ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    )
    daily_date_semantics: str = (
        "Provider trading-date label; no timezone localization is applied to daily dates."
    )
    intraday_naive_timestamp_policy: str = (
        "Preserve as timezone-naive with provider context; do not localize without evidence."
    )
    intraday_aware_timestamp_policy: str = "Preserve the source timezone and offset."
    sessions: list[SessionWindow] = Field(
        default_factory=lambda: [
            SessionWindow(
                name="opening_auction",
                start_local="09:00",
                end_local="09:15",
                matching_method="periodic",
            ),
            SessionWindow(
                name="continuous_morning",
                start_local="09:15",
                end_local="11:30",
                matching_method="continuous",
            ),
            SessionWindow(
                name="lunch_break",
                start_local="11:30",
                end_local="13:00",
                matching_method="none",
            ),
            SessionWindow(
                name="continuous_afternoon",
                start_local="13:00",
                end_local="14:30",
                matching_method="continuous",
            ),
            SessionWindow(
                name="closing_auction",
                start_local="14:30",
                end_local="14:45",
                matching_method="periodic",
            ),
        ]
    )
    negotiated_trading_end_local: str = "15:00"
    holiday_calendar_status: str = HOLIDAY_CALENDAR_STATUS
    limitations: list[str] = Field(
        default_factory=lambda: [
            "Expected sessions currently use weekdays only.",
            (
                "Vietnamese public holidays, exchange closures, and symbol-specific halts "
                "are not modeled."
            ),
            "Restricted-trading instruments may follow different matching schedules.",
        ]
    )
    sources: list[str] = Field(
        default_factory=lambda: [
            "https://staticfile.hsx.vn/Uploads/LocalFiles/993f05f252bb4bf0a755bcd51440f90f/20210704_Quy%20ch%E1%BA%BF%20giao%20d%E1%BB%8Bch.pdf",
            "https://staticfile.hsx.vn/Uploads/UploadDocuments/2372196/2.Thoi%20gian%20giao%20dich.pdf",
        ]
    )


def market_time_policy() -> MarketTimePolicy:
    return MarketTimePolicy()


def timestamp_provenance(value: Any, *, provider: str) -> TimestampProvenance:
    if value is None or pd.isna(value):
        return TimestampProvenance(
            provider=provider,
            original_value=None,
            parsed_value=None,
            awareness_status=TimestampAwarenessStatus.MISSING,
            interpretation="missing_provider_timestamp",
        )

    original = str(value)
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return TimestampProvenance(
            provider=provider,
            original_value=original,
            parsed_value=None,
            awareness_status=TimestampAwarenessStatus.INVALID,
            interpretation="invalid_provider_timestamp_preserved",
        )

    timestamp = pd.Timestamp(parsed)
    aware = timestamp.tzinfo is not None
    source_timezone = str(timestamp.tzinfo) if aware else None
    parsed_value = timestamp.to_pydatetime()
    if not isinstance(parsed_value, datetime):  # pragma: no cover - pandas guarantee.
        raise TypeError("Parsed timestamp did not produce a datetime.")
    return TimestampProvenance(
        provider=provider,
        original_value=original,
        parsed_value=parsed_value,
        awareness_status=(
            TimestampAwarenessStatus.AWARE if aware else TimestampAwarenessStatus.NAIVE
        ),
        source_timezone=source_timezone,
        localization_applied=False,
        interpretation=(
            "source_timezone_preserved" if aware else "timezone_naive_preserved_no_localization"
        ),
    )


def aware_timestamp_to_utc(value: Any, *, provider: str) -> pd.Timestamp | None:
    provenance = timestamp_provenance(value, provider=provider)
    if provenance.awareness_status is not TimestampAwarenessStatus.AWARE:
        return None
    return pd.Timestamp(provenance.parsed_value).tz_convert("UTC")
