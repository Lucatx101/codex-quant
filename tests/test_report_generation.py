from __future__ import annotations

import json

from hose_quant.data.models import (
    AuditReport,
    CapabilityResult,
    CapabilityStatus,
    PackageInspection,
)
from hose_quant.reporting import render_markdown_report, write_audit_reports


def make_report() -> AuditReport:
    return AuditReport(
        operating_system="Darwin test",
        python_version="3.13.0",
        package_inspection=PackageInspection(package_version="4.0.4"),
        authentication_state="VNSTOCK_API_KEY missing",
        documentation_sources=["https://vnstocks.com/docs"],
        tested_symbols=["FPT"],
        capabilities=[
            CapabilityResult(
                capability_name="daily historical OHLCV",
                status=CapabilityStatus.DOCUMENTED_NOT_TESTED,
                evidence_notes=["documented"],
            ),
        ],
        documented_free_tier_constraints=["community/free"],
        documented_rate_limit="unknown",
        unresolved_uncertainties=["live audit pending"],
        blocking_issues=["missing key"],
        conclusions={"daily_ohlcv_usable": "pending"},
        recommended_phase_1_scope=["run live audit"],
    )


def test_markdown_report_contains_required_sections_without_secret() -> None:
    markdown = render_markdown_report(make_report())
    assert "## Capability Summary" in markdown
    assert "## Recommended Data Architecture For Phase 1" in markdown
    assert "secret" not in markdown.lower()


def test_json_report_generation_is_deterministic_and_valid(tmp_path) -> None:
    report = make_report()
    json_path = tmp_path / "data_capabilities.json"
    markdown_path = tmp_path / "data-capability-report.md"
    write_audit_reports(report, json_path=json_path, markdown_path=markdown_path)
    first = json_path.read_text(encoding="utf-8")
    write_audit_reports(report, json_path=json_path, markdown_path=markdown_path)
    second = json_path.read_text(encoding="utf-8")
    assert first == second
    assert json.loads(first)["project_name"] == "hose-quant-system"


def test_live_report_does_not_claim_no_live_requests() -> None:
    report = make_report()
    report.authentication_state = "VNSTOCK_API_KEY set"
    report.capabilities = [
        CapabilityResult(
            capability_name="daily historical OHLCV",
            status=CapabilityStatus.VERIFIED,
            elapsed_latency_ms=100,
        )
    ]
    report.unresolved_uncertainties = [
        "Historical point-in-time universe membership was not verified."
    ]
    markdown = render_markdown_report(report)
    assert "No live API-key-backed requests were completed" not in markdown
    assert "Latency observations are listed per capability above." in markdown
