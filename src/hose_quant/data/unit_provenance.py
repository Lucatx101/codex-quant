from __future__ import annotations

from typing import Any

import pandas as pd
from pydantic import ValidationError

from hose_quant.data.models import (
    DailyUnitProvenance,
    LiquidityUnitPolicy,
    PriceUnit,
    TradedValueUnit,
    UnitProvenanceStatus,
    UnitVerificationStatus,
    VolumeUnit,
)

DAILY_UNIT_PROVENANCE_SCHEMA_VERSION = "daily-unit-provenance-v1"
UNVERIFIED_UNIT_POLICY_VERSION = "1"
VNSTOCK_KBS_DATA_BACKEND = "kbs"
VNSTOCK_KBS_UNIT_POLICY_NAME = "vnstock-kbs-daily-ohlcv"
VNSTOCK_KBS_UNIT_POLICY_VERSION = "1"
VNSTOCK_KBS_EVIDENCE_REFERENCE = "vnstock-kbs-ohlcv-units@2026-01-31"
VNSTOCK_VCI_DATA_BACKEND = "vci"
VNSTOCK_VCI_UNIT_POLICY_NAME = "vnstock-vci-daily-ohlcv"
VNSTOCK_VCI_UNIT_POLICY_VERSION = "1"
VNSTOCK_VCI_EVIDENCE_REFERENCE = "vnstock-vci-ohlcv-wrapper-units@2026-08-06"

VNSTOCK_KBS_DAILY_UNIT_PROVENANCE = DailyUnitProvenance(
    schema_version=DAILY_UNIT_PROVENANCE_SCHEMA_VERSION,
    provider="vnstock",
    data_backend=VNSTOCK_KBS_DATA_BACKEND,
    source_resolution="1D",
    unit_policy_name=VNSTOCK_KBS_UNIT_POLICY_NAME,
    unit_policy_version=VNSTOCK_KBS_UNIT_POLICY_VERSION,
    price_unit=PriceUnit.THOUSAND_VND,
    volume_unit=VolumeUnit.SHARES,
    price_scale_to_vnd=1000.0,
    volume_scale_to_shares=1.0,
    evidence_reference=VNSTOCK_KBS_EVIDENCE_REFERENCE,
)

VNSTOCK_VCI_DAILY_UNIT_PROVENANCE = DailyUnitProvenance(
    schema_version=DAILY_UNIT_PROVENANCE_SCHEMA_VERSION,
    provider="vnstock",
    data_backend=VNSTOCK_VCI_DATA_BACKEND,
    source_resolution="1D",
    unit_policy_name=VNSTOCK_VCI_UNIT_POLICY_NAME,
    unit_policy_version=VNSTOCK_VCI_UNIT_POLICY_VERSION,
    price_unit=PriceUnit.THOUSAND_VND,
    volume_unit=VolumeUnit.SHARES,
    price_scale_to_vnd=1000.0,
    volume_scale_to_shares=1.0,
    evidence_reference=VNSTOCK_VCI_EVIDENCE_REFERENCE,
)

REGISTERED_DAILY_UNIT_PROVENANCE = (
    VNSTOCK_KBS_DAILY_UNIT_PROVENANCE,
    VNSTOCK_VCI_DAILY_UNIT_PROVENANCE,
)

PROVENANCE_COLUMN_TO_FIELD = {
    "unit_provenance_schema_version": "schema_version",
    "provider": "provider",
    "data_backend": "data_backend",
    "source_resolution": "source_resolution",
    "source_unit_policy_name": "unit_policy_name",
    "source_unit_policy_version": "unit_policy_version",
    "source_price_unit": "price_unit",
    "source_volume_unit": "volume_unit",
    "source_price_scale_to_vnd": "price_scale_to_vnd",
    "source_volume_scale_to_shares": "volume_scale_to_shares",
    "source_unit_evidence_reference": "evidence_reference",
}
NORMALIZED_DAILY_PROVENANCE_COLUMNS = tuple(PROVENANCE_COLUMN_TO_FIELD)
SOURCE_SPECIFIC_PROVENANCE_COLUMNS = tuple(
    column
    for column in NORMALIZED_DAILY_PROVENANCE_COLUMNS
    if column not in {"provider", "source_resolution"}
)


def daily_provenance_columns(
    provenance: DailyUnitProvenance | None,
) -> dict[str, Any]:
    if provenance is None:
        return dict.fromkeys(SOURCE_SPECIFIC_PROVENANCE_COLUMNS, pd.NA)
    values = provenance.model_dump(mode="json")
    return {
        column: values[field]
        for column, field in PROVENANCE_COLUMN_TO_FIELD.items()
        if column not in {"provider", "source_resolution"}
    }


def ensure_daily_provenance_columns(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    for column in SOURCE_SPECIFIC_PROVENANCE_COLUMNS:
        if column not in output.columns:
            output[column] = pd.NA
    return output


def resolve_daily_unit_policy(frame: pd.DataFrame) -> LiquidityUnitPolicy:
    if frame.empty:
        return _unverified_policy(
            frame,
            status=UnitProvenanceStatus.NO_DATA,
            reason=(
                "No selected daily rows are available, so dataset unit provenance cannot be "
                "verified and VND traded value is disabled."
            ),
        )

    available_specific = {
        column for column in SOURCE_SPECIFIC_PROVENANCE_COLUMNS if column in frame.columns
    }
    if not available_specific:
        return _unverified_policy(
            frame,
            status=UnitProvenanceStatus.LEGACY_MISSING,
            reason=_legacy_reason(),
        )

    missing_columns = sorted(
        set(NORMALIZED_DAILY_PROVENANCE_COLUMNS) - set(map(str, frame.columns))
    )
    if missing_columns:
        return _unverified_policy(
            frame,
            status=UnitProvenanceStatus.INCOMPLETE,
            reason=(
                "Daily unit provenance is incomplete; missing columns: "
                f"{', '.join(missing_columns)}. VND traded value is disabled."
            ),
        )

    source_specific = frame[list(SOURCE_SPECIFIC_PROVENANCE_COLUMNS)]
    if bool(source_specific.isna().all(axis=None)):
        return _unverified_policy(
            frame,
            status=UnitProvenanceStatus.LEGACY_MISSING,
            reason=_legacy_reason(),
        )

    provenance_rows = frame[list(NORMALIZED_DAILY_PROVENANCE_COLUMNS)]
    complete_rows = provenance_rows.notna().all(axis=1)
    if not bool(complete_rows.all()):
        status = (
            UnitProvenanceStatus.AMBIGUOUS
            if bool(complete_rows.any())
            else UnitProvenanceStatus.INCOMPLETE
        )
        return _unverified_policy(
            frame,
            status=status,
            reason=(
                "Selected daily rows contain mixed or incomplete unit provenance. All rows must "
                "carry one identical registered provenance record; VND traded value is disabled."
            ),
        )

    distinct = provenance_rows.drop_duplicates(ignore_index=True)
    if len(distinct) != 1:
        return _unverified_policy(
            frame,
            status=UnitProvenanceStatus.AMBIGUOUS,
            reason=(
                "Selected daily rows contain more than one unit provenance record. Mixed-source "
                "monetary interpretation is not permitted."
            ),
        )

    record = distinct.iloc[0]
    payload = {
        field: _native_scalar(record[column])
        for column, field in PROVENANCE_COLUMN_TO_FIELD.items()
    }
    try:
        provenance = DailyUnitProvenance.model_validate(payload)
    except ValidationError as exc:
        return _unverified_policy(
            frame,
            status=UnitProvenanceStatus.INCOMPATIBLE,
            reason=(
                "Daily unit provenance is not a valid versioned record: "
                f"{exc.errors()[0]['msg']}. VND traded value is disabled."
            ),
        )

    matched = next(
        (registered for registered in REGISTERED_DAILY_UNIT_PROVENANCE if provenance == registered),
        None,
    )
    if matched is None:
        return _unverified_policy(
            frame,
            status=UnitProvenanceStatus.INCOMPATIBLE,
            reason=(
                "Daily unit provenance does not exactly match a registered provider/backend unit "
                "contract. VND traded value is disabled."
            ),
            source_provenance=provenance,
        )
    return _verified_policy(matched)


def effective_unit_metadata(policy: LiquidityUnitPolicy) -> dict[str, Any]:
    return {
        "unit_provenance_status": policy.provenance_status.value,
        "unit_verification_status": policy.verification_status.value,
        "unit_policy_name": policy.name,
        "unit_policy_version": policy.version,
        "price_unit": policy.price_unit.value,
        "volume_unit": policy.volume_unit.value,
        "traded_value_unit": policy.traded_value_unit.value,
        "unit_evidence_reference": policy.evidence_reference or pd.NA,
        "unit_verification_reason": policy.verification_reason,
        "vnd_traded_value_permitted": policy.vnd_traded_value_permitted,
    }


def unit_provenance_output_metadata(policy: LiquidityUnitPolicy) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "provider": policy.provider or pd.NA,
        "source_resolution": policy.source_resolution or pd.NA,
    }
    metadata.update(daily_provenance_columns(policy.source_provenance))
    if policy.data_backend is not None:
        metadata["data_backend"] = policy.data_backend
    metadata.update(effective_unit_metadata(policy))
    return metadata


def _verified_policy(provenance: DailyUnitProvenance) -> LiquidityUnitPolicy:
    return LiquidityUnitPolicy(
        name=provenance.unit_policy_name,
        version=provenance.unit_policy_version,
        provenance_status=UnitProvenanceStatus.VERIFIED,
        verification_status=UnitVerificationStatus.VERIFIED,
        provider=provenance.provider,
        data_backend=provenance.data_backend,
        source_resolution=provenance.source_resolution,
        source_provenance=provenance,
        price_unit=provenance.price_unit,
        volume_unit=provenance.volume_unit,
        price_scale_to_vnd=provenance.price_scale_to_vnd,
        volume_scale_to_shares=provenance.volume_scale_to_shares,
        traded_value_unit=TradedValueUnit.VND,
        evidence_reference=provenance.evidence_reference,
        verification_reason=(
            "Every selected daily row carries one identical provenance record that exactly "
            "matches a registered vnstock provider/backend daily OHLCV unit contract."
        ),
        vnd_traded_value_permitted=True,
    )


def _unverified_policy(
    frame: pd.DataFrame,
    *,
    status: UnitProvenanceStatus,
    reason: str,
    source_provenance: DailyUnitProvenance | None = None,
) -> LiquidityUnitPolicy:
    provider = source_provenance.provider if source_provenance else _uniform_text(frame, "provider")
    data_backend = (
        source_provenance.data_backend
        if source_provenance
        else _uniform_text(frame, "data_backend")
    )
    resolution = (
        source_provenance.source_resolution
        if source_provenance
        else _uniform_text(frame, "source_resolution")
    )
    evidence_reference = (
        source_provenance.evidence_reference
        if source_provenance
        else _uniform_text(frame, "source_unit_evidence_reference")
    )
    return LiquidityUnitPolicy(
        name="unverified",
        version=UNVERIFIED_UNIT_POLICY_VERSION,
        provenance_status=status,
        verification_status=UnitVerificationStatus.UNVERIFIED,
        provider=provider,
        data_backend=data_backend,
        source_resolution=resolution,
        source_provenance=source_provenance,
        price_unit=PriceUnit.UNKNOWN,
        volume_unit=VolumeUnit.PROVIDER_UNITS,
        traded_value_unit=TradedValueUnit.UNAVAILABLE,
        evidence_reference=evidence_reference,
        verification_reason=reason,
        vnd_traded_value_permitted=False,
    )


def _uniform_text(frame: pd.DataFrame, column: str) -> str | None:
    if column not in frame.columns:
        return None
    values = frame[column].dropna().astype(str).str.strip()
    unique = values[values != ""].unique().tolist()
    return unique[0] if len(unique) == 1 else None


def _native_scalar(value: Any) -> Any:
    item = getattr(value, "item", None)
    return item() if callable(item) else value


def _legacy_reason() -> str:
    return (
        "Legacy daily data has no source-specific, versioned unit provenance. It remains usable "
        "for OHLCV panels and non-monetary liquidity metrics, but must be re-ingested through a "
        "provenance-aware provider normalizer before VND traded value is permitted."
    )
