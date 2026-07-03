from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from hose_quant.config import MissingCredentialError, load_settings
from hose_quant.data.vnstock_adapter import VnstockCapabilityAuditor
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

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
