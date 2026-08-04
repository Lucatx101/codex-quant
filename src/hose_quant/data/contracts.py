from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from hose_quant.data.market_time import MARKET_TIME_POLICY_VERSION

UNIVERSE_CONTRACT_VERSION = "research-universe-v1"
DAILY_PANEL_CONTRACT_VERSION = "daily-panel-v2"
LIQUIDITY_CONTRACT_VERSION = "liquidity-characterization-v2"
AVAILABILITY_CONTRACT_VERSION = "daily-availability-v1"


class DataContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    key_columns: list[str]
    ordering: list[str]
    required_columns: list[str]
    nullable_columns: list[str] = Field(default_factory=list)
    semantics: dict[str, Any] = Field(default_factory=dict)


RESEARCH_UNIVERSE_CONTRACT = DataContract(
    name="research_universe",
    version=UNIVERSE_CONTRACT_VERSION,
    key_columns=["source_snapshot_observed_at_utc", "input_row_number"],
    ordering=["candidate_status", "symbol", "input_row_number"],
    required_columns=[
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
    ],
    nullable_columns=["symbol", "requested_reference_date"],
    semantics={
        "membership": "Current provider snapshot only; historical membership is not verified.",
        "candidate": "Included means research candidate, not confirmed active or tradable.",
        "row_preservation": "Every selected normalized input row has one auditable output row.",
    },
)

DAILY_PANEL_CONTRACT = DataContract(
    name="daily_panel",
    version=DAILY_PANEL_CONTRACT_VERSION,
    key_columns=["symbol", "date"],
    ordering=["symbol", "date"],
    required_columns=[
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
    ],
    nullable_columns=[
        "exchange",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "source_adjusted_flag",
        "data_backend",
        "unit_provenance_schema_version",
        "source_unit_policy_name",
        "source_unit_policy_version",
        "source_price_unit",
        "source_volume_unit",
        "source_price_scale_to_vnd",
        "source_volume_scale_to_shares",
        "source_unit_evidence_reference",
        "unit_evidence_reference",
    ],
    semantics={
        "missingness": "Observed rows only; no bars are synthesized or forward-filled.",
        "adjustment": "Unknown unless independently verified; no adjustment is constructed.",
        "daily_dates": "Provider date labels are not localized to a timezone.",
        "market_time_contract": MARKET_TIME_POLICY_VERSION,
    },
)

LIQUIDITY_CONTRACT = DataContract(
    name="liquidity_characterization",
    version=LIQUIDITY_CONTRACT_VERSION,
    key_columns=["symbol", "reference_date"],
    ordering=["symbol"],
    required_columns=[
        "feature_input_contract_version",
        "provider",
        "data_backend",
        "source_resolution",
        "unit_provenance_schema_version",
        "source_unit_policy_name",
        "source_unit_policy_version",
        "source_price_unit",
        "source_volume_unit",
        "source_price_scale_to_vnd",
        "source_volume_scale_to_shares",
        "source_unit_evidence_reference",
        "symbol",
        "reference_date",
        "window_start_date",
        "window_weekdays",
        "observed_date_count",
        "out_of_weekday_calendar_observation_count",
        "trading_frequency",
        "zero_volume_frequency",
        "average_volume_provider_units",
        "average_traded_value_vnd",
        "recent_valid_close",
        "recent_valid_close_date",
        "insufficient_history",
        "missing_data_status",
        "screen_status",
        "screen_reasons",
        "unit_provenance_status",
        "unit_verification_status",
        "price_unit",
        "volume_unit",
        "traded_value_unit",
        "unit_policy_name",
        "unit_policy_version",
        "unit_evidence_reference",
        "unit_verification_reason",
        "vnd_traded_value_permitted",
    ],
    nullable_columns=[
        "provider",
        "source_resolution",
        "trading_frequency",
        "zero_volume_frequency",
        "average_volume_provider_units",
        "average_traded_value_vnd",
        "recent_valid_close",
        "recent_valid_close_date",
        "data_backend",
        "unit_provenance_schema_version",
        "source_unit_policy_name",
        "source_unit_policy_version",
        "source_price_unit",
        "source_volume_unit",
        "source_price_scale_to_vnd",
        "source_volume_scale_to_shares",
        "source_unit_evidence_reference",
        "unit_evidence_reference",
    ],
    semantics={
        "causality": "Uses only observations on or before reference_date.",
        "window": "Trailing expected weekdays; Vietnam holidays are not removed.",
        "money": (
            "VND traded value exists only when selected input rows carry one matching, "
            "registered provider/backend unit-provenance record."
        ),
    },
)

AVAILABILITY_CONTRACT = DataContract(
    name="daily_availability",
    version=AVAILABILITY_CONTRACT_VERSION,
    key_columns=["symbol", "requested_start_date", "requested_end_date"],
    ordering=["symbol"],
    required_columns=[
        "feature_input_contract_version",
        "symbol",
        "requested_start_date",
        "requested_end_date",
        "observed_start_date",
        "observed_end_date",
        "observation_count",
        "duplicate_count",
        "missing_ohlc_count",
        "invalid_ohlc_count",
        "zero_volume_count",
        "expected_weekday_count",
        "observed_expected_weekday_count",
        "missing_expected_weekday_count",
        "weekday_coverage_ratio",
        "absence_of_data",
        "expected_session_model",
        "holiday_calendar_status",
    ],
    nullable_columns=["observed_start_date", "observed_end_date", "weekday_coverage_ratio"],
    semantics={
        "expected_sessions": "Weekdays only; public holidays and closures are not modeled.",
        "missingness": "Missing weekdays remain diagnostic facts, not synthetic panel rows.",
    },
)


def contract_versions() -> dict[str, str]:
    return {
        "research_universe": UNIVERSE_CONTRACT_VERSION,
        "daily_panel": DAILY_PANEL_CONTRACT_VERSION,
        "liquidity": LIQUIDITY_CONTRACT_VERSION,
        "availability": AVAILABILITY_CONTRACT_VERSION,
        "market_time": MARKET_TIME_POLICY_VERSION,
    }
