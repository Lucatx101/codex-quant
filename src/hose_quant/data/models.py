from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


class CapabilityStatus(StrEnum):
    VERIFIED = "VERIFIED"
    DOCUMENTED_NOT_TESTED = "DOCUMENTED_NOT_TESTED"
    UNAVAILABLE_FREE_TIER = "UNAVAILABLE_FREE_TIER"
    UNAVAILABLE_PACKAGE = "UNAVAILABLE_PACKAGE"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    NETWORK_ERROR = "NETWORK_ERROR"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    INVALID_SCHEMA = "INVALID_SCHEMA"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    UNKNOWN = "UNKNOWN"


class ErrorCategory(StrEnum):
    NONE = "NONE"
    AUTHENTICATION = "AUTHENTICATION"
    RATE_LIMIT = "RATE_LIMIT"
    NETWORK = "NETWORK"
    TIMEOUT = "TIMEOUT"
    PROVIDER = "PROVIDER"
    INVALID_SCHEMA = "INVALID_SCHEMA"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    PACKAGE_NOT_INSTALLED = "PACKAGE_NOT_INSTALLED"
    UNKNOWN = "UNKNOWN"


class PackageInspection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_name: str = "vnstock"
    package_version: str = "unknown"
    python_requires: str | None = None
    module_path: str | None = None
    top_level_symbols: list[str] = Field(default_factory=list)
    supported_providers_found: list[str] = Field(default_factory=list)
    authentication_mechanism: str = "VNSTOCK_API_KEY environment variable for this project"
    import_notes: list[str] = Field(default_factory=list)


class FrameInspection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_count: int = 0
    earliest_timestamp: str | None = None
    latest_timestamp: str | None = None
    timezone_information: str | None = None
    schema_summary: dict[str, str] = Field(default_factory=dict)
    data_quality_findings: list[str] = Field(default_factory=list)
    error_category: ErrorCategory = ErrorCategory.NONE


class CapabilityResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_name: str
    status: CapabilityStatus
    provider: str = "vnstock"
    authentication_tier: str = "community/free or unknown"
    package_name: str = "vnstock"
    package_version: str = "unknown"
    library_method: str | None = None
    tested_symbols: list[str] = Field(default_factory=list)
    request_timestamp: datetime = Field(default_factory=utc_now)
    elapsed_latency_ms: float | None = None
    returned_row_count: int | None = None
    earliest_timestamp: str | None = None
    latest_timestamp: str | None = None
    timezone_information: str | None = None
    schema_summary: dict[str, str] = Field(default_factory=dict)
    data_quality_findings: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    error_category: ErrorCategory = ErrorCategory.NONE
    sanitized_error_message: str | None = None
    evidence_notes: list[str] = Field(default_factory=list)


class AuditReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_name: str = "hose-quant-system"
    execution_timestamp: datetime = Field(default_factory=utc_now)
    operating_system: str
    python_version: str
    package_inspection: PackageInspection
    authentication_state: str
    documentation_sources: list[str]
    tested_symbols: list[str]
    capabilities: list[CapabilityResult]
    documented_free_tier_constraints: list[str] = Field(default_factory=list)
    documented_rate_limit: str | None = None
    unresolved_uncertainties: list[str] = Field(default_factory=list)
    blocking_issues: list[str] = Field(default_factory=list)
    conclusions: dict[str, str] = Field(default_factory=dict)
    recommended_phase_1_scope: list[str] = Field(default_factory=list)

    def capability_counts(self) -> dict[str, int]:
        counts = {status.value: 0 for status in CapabilityStatus}
        for capability in self.capabilities:
            counts[capability.status.value] += 1
        return counts


def result_from_frame(
    *,
    capability_name: str,
    status: CapabilityStatus,
    package_version: str,
    library_method: str,
    tested_symbols: list[str],
    frame: FrameInspection,
    elapsed_latency_ms: float | None = None,
    evidence_notes: list[str] | None = None,
    limitations: list[str] | None = None,
) -> CapabilityResult:
    return CapabilityResult(
        capability_name=capability_name,
        status=status,
        package_version=package_version,
        library_method=library_method,
        tested_symbols=tested_symbols,
        elapsed_latency_ms=elapsed_latency_ms,
        returned_row_count=frame.row_count,
        earliest_timestamp=frame.earliest_timestamp,
        latest_timestamp=frame.latest_timestamp,
        timezone_information=frame.timezone_information,
        schema_summary=frame.schema_summary,
        data_quality_findings=frame.data_quality_findings,
        limitations=limitations or [],
        error_category=frame.error_category,
        evidence_notes=evidence_notes or [],
    )


def model_to_jsonable(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")
