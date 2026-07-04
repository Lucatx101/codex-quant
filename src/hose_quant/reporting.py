from __future__ import annotations

import json
from pathlib import Path

from hose_quant.data.models import AuditReport, model_to_jsonable


def write_audit_reports(
    report: AuditReport,
    *,
    json_path: Path,
    markdown_path: Path,
) -> tuple[Path, Path]:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(model_to_jsonable(report), ensure_ascii=False, indent=2, sort_keys=True)
    json_path.write_text(payload + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown_report(report), encoding="utf-8")
    return json_path, markdown_path


def render_markdown_report(report: AuditReport) -> str:
    counts = report.capability_counts()
    lines = [
        "# Phase 0 Data Capability Report",
        "",
        "## Execution",
        "",
        f"- Timestamp: {report.execution_timestamp.isoformat()}",
        f"- Operating system: {report.operating_system}",
        f"- Python: {report.python_version}",
        (
            f"- Package: {report.package_inspection.package_name} "
            f"{report.package_inspection.package_version}"
        ),
        f"- Authentication: {report.authentication_state}",
        "",
        "## Documentation Sources",
        "",
    ]
    lines.extend(f"- {source}" for source in report.documentation_sources)
    lines.extend(
        [
            "",
            "## Documented Free-Tier Constraints",
            "",
        ],
    )
    lines.extend(f"- {item}" for item in report.documented_free_tier_constraints)
    lines.extend(
        [
            f"- Documented or observed rate limit: {report.documented_rate_limit or 'unknown'}",
            "",
            "## Capability Summary",
            "",
        ],
    )
    lines.extend(f"- {status}: {count}" for status, count in counts.items() if count)
    lines.extend(["", "## Detailed Findings", ""])
    for capability in report.capabilities:
        tested_symbols = (
            ", ".join(capability.tested_symbols) if capability.tested_symbols else "none"
        )
        returned_rows = (
            capability.returned_row_count
            if capability.returned_row_count is not None
            else "not tested"
        )
        latency = (
            capability.elapsed_latency_ms
            if capability.elapsed_latency_ms is not None
            else "not tested"
        )
        lines.extend(
            [
                f"### {capability.capability_name}",
                "",
                f"- Status: {capability.status.value}",
                f"- Method or endpoint: {capability.library_method or 'not verified'}",
                f"- Tested symbols: {tested_symbols}",
                f"- Returned rows: {returned_rows}",
                f"- Latency: {latency} ms",
                f"- Earliest timestamp: {capability.earliest_timestamp or 'not available'}",
                f"- Latest timestamp: {capability.latest_timestamp or 'not available'}",
                f"- Timezone: {capability.timezone_information or 'not available'}",
                f"- Error category: {capability.error_category.value}",
            ],
        )
        if capability.schema_summary:
            schema = ", ".join(
                f"{key}: {value}" for key, value in capability.schema_summary.items()
            )
            lines.append(f"- Schema: {schema}")
        if capability.data_quality_findings:
            lines.append("- Data quality findings: " + "; ".join(capability.data_quality_findings))
        if capability.limitations:
            lines.append("- Limitations: " + "; ".join(capability.limitations))
        if capability.evidence_notes:
            lines.append("- Evidence: " + "; ".join(capability.evidence_notes))
        if capability.sanitized_error_message:
            lines.append(f"- Sanitized error: {capability.sanitized_error_message}")
        lines.append("")

    lines.extend(
        [
            "## Schema And Timestamp Findings",
            "",
            (
                "Schema and timestamp findings are listed per capability above. Live schema "
                "validation remains pending for documented-only capabilities."
            ),
            "",
            "## Latency Observations",
            "",
            _latency_observation_text(report),
            "",
            "## Data-Quality Problems",
            "",
        ],
    )
    quality = [
        f"{cap.capability_name}: {'; '.join(cap.data_quality_findings)}"
        for cap in report.capabilities
        if cap.data_quality_findings
    ]
    lines.extend(f"- {item}" for item in quality or ["No live data-quality findings recorded."])
    lines.extend(
        [
            "",
            "## Rate-Limit Implications",
            "",
            (
                "- 20 symbols: reasonable initial ceiling for live polling until latency "
                "and quota are verified."
            ),
            "- 50 symbols: requires batch quote evidence and caching before use.",
            "- 100 symbols: not recommended on the free tier without measured headroom.",
            "- Full HOSE universe: likely requires paid or alternative data for automated polling.",
            "",
            "## Unresolved Uncertainties",
            "",
        ],
    )
    lines.extend(f"- {item}" for item in report.unresolved_uncertainties)
    lines.extend(["", "## Blocking Issues", ""])
    lines.extend(f"- {item}" for item in report.blocking_issues or ["None recorded."])
    lines.extend(["", "## Conclusions", ""])
    for key, value in report.conclusions.items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Recommended Data Architecture For Phase 1", ""])
    lines.extend(f"- {item}" for item in report.recommended_phase_1_scope)
    lines.append("")
    return "\n".join(lines)


def _latency_observation_text(report: AuditReport) -> str:
    if any(capability.elapsed_latency_ms is not None for capability in report.capabilities):
        return "Latency observations are listed per capability above."
    return "No latency observations are available unless a live audit has been executed."
