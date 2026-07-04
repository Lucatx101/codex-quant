from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from hose_quant.config import AppSettings, MissingCredentialError, load_settings
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

    data = subparsers.add_parser("data", help="Run Phase 1 data-foundation commands.")
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
        else:
            raise ValueError(f"Unknown data command: {args.data_command}")
    except (SafetyLimitError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"Status: {result.manifest.status}")
    print(f"Run ID: {result.manifest.run_id}")
    if result.manifest_path:
        print(f"Manifest: {result.manifest_path}")
    for path in result.manifest.output_paths:
        print(f"Output: {path}")
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


if __name__ == "__main__":
    raise SystemExit(main())
