from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from hose_quant.config import AppSettings, MissingCredentialError, load_settings
from hose_quant.data.models import (
    DailyCampaignReadinessPolicy,
    DailyCoverageConfig,
    LiquidityScreenConfig,
)
from hose_quant.data.validators import has_blocking_errors
from hose_quant.data.vnstock_adapter import VnstockCapabilityAuditor
from hose_quant.data.workflows import DataWorkflow, SafetyLimitError
from hose_quant.logging import configure_logging
from hose_quant.reporting import write_audit_reports


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hose-quant")
    subparsers = parser.add_subparsers(dest="command", required=True)
    audit = subparsers.add_parser("audit-data", help="Run vnstock data-capability audit.")
    audit.add_argument(
        "--offline",
        action="store_true",
        help="Generate an unverified capability report without live provider calls.",
    )
    audit.add_argument("--json-path", type=Path, default=None)
    audit.add_argument("--markdown-path", type=Path, default=None)

    data = subparsers.add_parser("data", help="Run provider data and local feature-input commands.")
    data_subparsers = data.add_subparsers(dest="data_command", required=True)

    universe = data_subparsers.add_parser(
        "fetch-universe", help="Fetch and normalize a current exchange universe snapshot."
    )
    universe.add_argument("--exchange", default="HOSE")
    universe.add_argument("--dry-run", action="store_true")

    daily = data_subparsers.add_parser(
        "backfill-daily", help="Backfill daily OHLCV for a small symbol list."
    )
    daily.add_argument(
        "--symbols", required=True, help="Comma-separated symbols, e.g. FPT,HPG,VCB."
    )
    daily.add_argument("--start", required=True, help="Start date as YYYY-MM-DD.")
    daily.add_argument("--end", required=True, help="End date as YYYY-MM-DD.")
    daily.add_argument(
        "--chunk-calendar-days",
        type=int,
        default=None,
        help=(
            "Maximum calendar days per provider request; defaults to the configured safe "
            "two-year chunk."
        ),
    )
    daily.add_argument("--allow-large-universe", action="store_true")
    daily.add_argument("--dry-run", action="store_true")

    intraday = data_subparsers.add_parser(
        "fetch-intraday", help="Fetch intraday bars for a small symbol list."
    )
    intraday.add_argument("--symbols", required=True, help="Comma-separated symbols.")
    intraday.add_argument("--resolution", default="1m")
    intraday.add_argument("--lookback-days", type=int, default=1)
    intraday.add_argument("--allow-large-universe", action="store_true")
    intraday.add_argument("--dry-run", action="store_true")

    quotes = data_subparsers.add_parser(
        "snapshot-quotes", help="Fetch a batch latest-quote snapshot."
    )
    quotes.add_argument("--symbols", required=True, help="Comma-separated symbols.")
    quotes.add_argument("--allow-large-universe", action="store_true")
    quotes.add_argument("--dry-run", action="store_true")

    validate = data_subparsers.add_parser(
        "validate", help="Validate existing normalized datasets and write data-quality reports."
    )
    validate.add_argument(
        "--dry-run", action="store_true", help="Do not write data-quality reports."
    )

    prepare = data_subparsers.add_parser(
        "prepare-universe",
        help="Prepare an auditable research universe from a local normalized snapshot.",
        description=(
            "Prepare an auditable research universe. Monetary liquidity is enabled only when "
            "every selected daily row contains matching registered provider/backend provenance."
        ),
        epilog=(
            "Legacy daily files remain usable for panels and non-monetary liquidity metrics, "
            "but a VND threshold fails until data is re-ingested with versioned provenance."
        ),
    )
    prepare.add_argument("--exchange", default="HOSE")
    prepare.add_argument(
        "--snapshot-date",
        default=None,
        help="Observed snapshot date to select; defaults to the latest local snapshot.",
    )
    prepare.add_argument(
        "--reference-date",
        default=None,
        help=(
            "Optional research reference date recorded as unverified membership metadata; "
            "it does not backdate the snapshot."
        ),
    )
    prepare.add_argument("--with-liquidity", action="store_true")
    prepare.add_argument(
        "--daily-run-id",
        default=None,
        help=(
            "Use normalized daily files from exactly one ingestion run, avoiding legacy/new "
            "provenance mixing."
        ),
    )
    prepare.add_argument("--liquidity-reference-date", default=None)
    prepare.add_argument("--window-weekdays", type=int, default=None)
    prepare.add_argument("--min-history-observations", type=int, default=None)
    prepare.add_argument("--min-trading-frequency", type=float, default=None)
    prepare.add_argument("--max-zero-volume-frequency", type=float, default=None)
    prepare.add_argument("--min-average-volume", type=float, default=None)
    prepare.add_argument(
        "--min-average-traded-value-vnd",
        type=float,
        default=None,
        help=(
            "Minimum average traded value in VND. Requires matching machine-checkable unit "
            "provenance in the selected normalized daily rows."
        ),
    )

    panel = data_subparsers.add_parser(
        "build-daily-panel",
        help="Build a validated long-form daily panel and availability diagnostics locally.",
        description=(
            "Build a local daily panel. Unit verification is derived from stored daily-row "
            "provider/backend provenance and cannot be selected from the CLI."
        ),
        epilog=(
            "Legacy files without versioned provenance are preserved with unverified units; "
            "their OHLCV values remain available for non-monetary analysis."
        ),
    )
    panel.add_argument(
        "--symbols",
        default=None,
        help="Optional comma-separated symbols; defaults to all locally stored daily symbols.",
    )
    panel.add_argument("--start", required=True, help="Start date as YYYY-MM-DD.")
    panel.add_argument("--end", required=True, help="End date as YYYY-MM-DD.")
    panel.add_argument(
        "--daily-run-id",
        default=None,
        help="Use normalized daily files from exactly one ingestion run.",
    )

    coverage = data_subparsers.add_parser(
        "audit-daily-coverage",
        help="Audit one successful local daily ingestion run against a HOSE snapshot.",
        description=(
            "Audit coverage, quality, staleness, and unit provenance without provider calls. "
            "The source must be exactly one successful data backfill-daily run."
        ),
    )
    coverage.add_argument(
        "--daily-run-id",
        required=True,
        help="Exact successful backfill-daily run ID to audit.",
    )
    coverage.add_argument("--start", required=True, help="Audit start date as YYYY-MM-DD.")
    coverage.add_argument("--end", required=True, help="Audit end date as YYYY-MM-DD.")
    coverage.add_argument(
        "--snapshot-date",
        default=None,
        help="Observed universe snapshot date; defaults to the latest local snapshot.",
    )
    coverage.add_argument("--min-history-observations", type=int, default=None)
    coverage.add_argument("--min-span-coverage-ratio", type=float, default=None)
    coverage.add_argument("--stale-after-calendar-days", type=int, default=None)
    coverage.add_argument("--max-zero-volume-frequency", type=float, default=None)

    campaign_init = data_subparsers.add_parser(
        "init-daily-campaign",
        help="Create an immutable daily-ingestion plan from an observed HOSE snapshot.",
    )
    campaign_init.add_argument("--campaign-id", required=True)
    campaign_init.add_argument(
        "--snapshot-date",
        default=None,
        help="Observed universe snapshot date; defaults to the latest local snapshot.",
    )
    campaign_init.add_argument("--start", required=True, help="Start date as YYYY-MM-DD.")
    campaign_init.add_argument("--end", required=True, help="End date as YYYY-MM-DD.")
    campaign_init.add_argument("--chunk-calendar-days", type=int, default=None)

    campaign_adopt = data_subparsers.add_parser(
        "adopt-daily-run",
        help="Attach a compatible successful backfill run to campaign tasks.",
    )
    campaign_adopt.add_argument("--campaign-id", required=True)
    campaign_adopt.add_argument("--daily-run-id", required=True)

    campaign_run = data_subparsers.add_parser(
        "run-daily-campaign",
        help="Run the next resumable provider-limited campaign task batch.",
    )
    campaign_run.add_argument("--campaign-id", required=True)
    campaign_run.add_argument("--max-tasks", type=int, default=None)
    campaign_run.add_argument("--retry-failed", action="store_true")
    campaign_run.add_argument("--retry-stale", action="store_true")
    campaign_run.add_argument("--retry-incompatible", action="store_true")
    campaign_run.add_argument("--dry-run", action="store_true")

    campaign_audit = data_subparsers.add_parser(
        "audit-daily-campaign",
        help="Audit campaign state and virtual compatible coverage without provider calls.",
    )
    campaign_audit.add_argument("--campaign-id", required=True)
    campaign_audit.add_argument("--min-history-observations", type=int, default=None)
    campaign_audit.add_argument("--min-span-coverage-ratio", type=float, default=None)
    campaign_audit.add_argument("--stale-after-calendar-days", type=int, default=None)
    campaign_audit.add_argument("--max-zero-volume-frequency", type=float, default=None)
    campaign_audit.add_argument("--min-vnd-usable-symbol-ratio", type=float, default=None)
    campaign_audit.add_argument("--max-absent-symbol-ratio", type=float, default=None)

    campaign_assemble = data_subparsers.add_parser(
        "assemble-daily-campaign",
        help="Publish one versioned daily dataset after every campaign task resolves.",
    )
    campaign_assemble.add_argument("--campaign-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ValidationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    secret = settings.vnstock_api_key.get_secret_value() if settings.vnstock_api_key else None
    configure_logging(settings.log_level, secrets=[secret] if secret else [])

    if args.command == "audit-data":
        live = not args.offline
        if live:
            try:
                settings.require_vnstock_api_key()
            except MissingCredentialError as exc:
                print(str(exc), file=sys.stderr)
                return 2
        auditor = VnstockCapabilityAuditor(settings)
        try:
            report = auditor.audit_capabilities(live=live)
        except MissingCredentialError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        json_path = args.json_path or settings.report_dir / "data_capabilities.json"
        markdown_path = (
            args.markdown_path or settings.report_dir.parent / "docs" / "data-capability-report.md"
        )
        written_json, written_markdown = write_audit_reports(
            report,
            json_path=json_path,
            markdown_path=markdown_path,
        )
        print(f"Wrote JSON report: {written_json}")
        print(f"Wrote Markdown report: {written_markdown}")
        return 0

    if args.command == "data":
        return _run_data_command(args, settings)

    parser.error("unknown command")
    return 2


def _run_data_command(args: argparse.Namespace, settings: AppSettings) -> int:
    live_command = args.data_command in {
        "fetch-universe",
        "backfill-daily",
        "fetch-intraday",
        "snapshot-quotes",
        "run-daily-campaign",
    }
    if live_command and not args.dry_run:
        try:
            settings.require_vnstock_api_key()
        except MissingCredentialError as exc:
            print(str(exc), file=sys.stderr)
            return 2

    workflow = DataWorkflow(settings)
    try:
        if args.data_command == "fetch-universe":
            result = workflow.fetch_universe(exchange=args.exchange, dry_run=args.dry_run)
        elif args.data_command == "backfill-daily":
            result = workflow.backfill_daily(
                symbols=_split_symbols(args.symbols),
                start=_parse_date(args.start),
                end=_parse_date(args.end),
                chunk_calendar_days=args.chunk_calendar_days,
                allow_large_universe=args.allow_large_universe,
                dry_run=args.dry_run,
            )
        elif args.data_command == "fetch-intraday":
            result = workflow.fetch_intraday(
                symbols=_split_symbols(args.symbols),
                resolution=args.resolution,
                lookback_days=args.lookback_days,
                allow_large_universe=args.allow_large_universe,
                dry_run=args.dry_run,
            )
        elif args.data_command == "snapshot-quotes":
            result = workflow.snapshot_quotes(
                symbols=_split_symbols(args.symbols),
                allow_large_universe=args.allow_large_universe,
                dry_run=args.dry_run,
            )
        elif args.data_command == "validate":
            result = workflow.validate_existing_data(write_reports=not args.dry_run)
        elif args.data_command == "prepare-universe":
            result = workflow.prepare_universe(
                exchange=args.exchange,
                snapshot_date=_parse_optional_date(args.snapshot_date),
                requested_reference_date=_parse_optional_date(args.reference_date),
                with_liquidity=args.with_liquidity,
                liquidity_reference_date=_parse_optional_date(args.liquidity_reference_date),
                liquidity_config=_liquidity_config(args, settings),
                daily_run_id=args.daily_run_id,
            )
        elif args.data_command == "build-daily-panel":
            result = workflow.build_daily_panel(
                symbols=_split_symbols(args.symbols) if args.symbols else None,
                start=_parse_date(args.start),
                end=_parse_date(args.end),
                daily_run_id=args.daily_run_id,
            )
        elif args.data_command == "audit-daily-coverage":
            result = workflow.audit_daily_coverage(
                daily_run_id=args.daily_run_id,
                start=_parse_date(args.start),
                end=_parse_date(args.end),
                snapshot_date=_parse_optional_date(args.snapshot_date),
                config=_daily_coverage_config(args, settings),
            )
        elif args.data_command == "init-daily-campaign":
            result = workflow.init_daily_campaign(
                campaign_id=args.campaign_id,
                snapshot_date=_parse_optional_date(args.snapshot_date),
                start=_parse_date(args.start),
                end=_parse_date(args.end),
                chunk_calendar_days=args.chunk_calendar_days,
            )
        elif args.data_command == "adopt-daily-run":
            result = workflow.adopt_daily_run(
                campaign_id=args.campaign_id,
                daily_run_id=args.daily_run_id,
            )
        elif args.data_command == "run-daily-campaign":
            result = workflow.run_daily_campaign(
                campaign_id=args.campaign_id,
                max_tasks=args.max_tasks,
                retry_failed=args.retry_failed,
                retry_stale=args.retry_stale,
                retry_incompatible=args.retry_incompatible,
                dry_run=args.dry_run,
            )
        elif args.data_command == "audit-daily-campaign":
            result = workflow.audit_daily_campaign(
                campaign_id=args.campaign_id,
                config=_daily_coverage_config(args, settings),
                readiness_policy=_daily_campaign_readiness_policy(args, settings),
            )
        elif args.data_command == "assemble-daily-campaign":
            result = workflow.assemble_daily_campaign(campaign_id=args.campaign_id)
        else:
            raise ValueError(f"Unknown data command: {args.data_command}")
    except (SafetyLimitError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"Status: {result.manifest.status}")
    print(f"Run ID: {result.manifest.run_id}")
    if result.manifest.dry_run:
        for key, value in sorted(result.manifest.parameters.items()):
            print(f"Plan {key}: {value}")
    if result.manifest_path:
        print(f"Manifest: {result.manifest_path}")
    for path in result.manifest.output_paths:
        print(f"Output: {path}")
    campaign_status_labels = {
        "campaign_complete": "Campaign complete",
        "assembly_compatible": "Assembly compatible",
        "assembly_ready": "Assembly ready",
        "coverage_quality_status": "Coverage-quality status",
        "research_readiness_status": "Research-readiness status",
        "canonical_candidate": "Canonical candidate",
    }
    for key, label in campaign_status_labels.items():
        if key in result.manifest.parameters:
            print(f"{label}: {result.manifest.parameters[key]}")
    if result.manifest.error_summary:
        for error in result.manifest.error_summary:
            print(f"Error: {error}", file=sys.stderr)
    if has_blocking_errors(result.validation_results) or result.manifest.status == "failed":
        return 1
    return 0


def _split_symbols(value: str) -> list[str]:
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _parse_optional_date(value: str | None) -> date | None:
    return _parse_date(value) if value else None


def _liquidity_config(
    args: argparse.Namespace,
    settings: AppSettings,
) -> LiquidityScreenConfig:
    return LiquidityScreenConfig(
        window_weekdays=(
            args.window_weekdays
            if args.window_weekdays is not None
            else settings.liquidity_window_weekdays
        ),
        min_history_observations=(
            args.min_history_observations
            if args.min_history_observations is not None
            else settings.liquidity_min_history_observations
        ),
        min_trading_frequency=(
            args.min_trading_frequency
            if args.min_trading_frequency is not None
            else settings.liquidity_min_trading_frequency
        ),
        max_zero_volume_frequency=(
            args.max_zero_volume_frequency
            if args.max_zero_volume_frequency is not None
            else settings.liquidity_max_zero_volume_frequency
        ),
        min_average_volume_provider_units=(
            args.min_average_volume
            if args.min_average_volume is not None
            else settings.liquidity_min_average_volume
        ),
        min_average_traded_value_vnd=(
            args.min_average_traded_value_vnd
            if args.min_average_traded_value_vnd is not None
            else settings.liquidity_min_average_traded_value_vnd
        ),
    )


def _daily_coverage_config(
    args: argparse.Namespace,
    settings: AppSettings,
) -> DailyCoverageConfig:
    return DailyCoverageConfig(
        min_history_observations=(
            args.min_history_observations
            if args.min_history_observations is not None
            else settings.daily_coverage_min_history_observations
        ),
        min_span_coverage_ratio=(
            args.min_span_coverage_ratio
            if args.min_span_coverage_ratio is not None
            else settings.daily_coverage_min_span_ratio
        ),
        stale_after_calendar_days=(
            args.stale_after_calendar_days
            if args.stale_after_calendar_days is not None
            else settings.daily_coverage_stale_after_days
        ),
        max_zero_volume_frequency=(
            args.max_zero_volume_frequency
            if args.max_zero_volume_frequency is not None
            else settings.daily_coverage_max_zero_volume_frequency
        ),
    )


def _daily_campaign_readiness_policy(
    args: argparse.Namespace,
    settings: AppSettings,
) -> DailyCampaignReadinessPolicy:
    return DailyCampaignReadinessPolicy(
        min_vnd_usable_symbol_ratio=(
            args.min_vnd_usable_symbol_ratio
            if args.min_vnd_usable_symbol_ratio is not None
            else settings.campaign_readiness_min_vnd_usable_symbol_ratio
        ),
        max_absent_symbol_ratio=(
            args.max_absent_symbol_ratio
            if args.max_absent_symbol_ratio is not None
            else settings.campaign_readiness_max_absent_symbol_ratio
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
