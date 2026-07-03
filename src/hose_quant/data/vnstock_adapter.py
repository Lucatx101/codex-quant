from __future__ import annotations

import contextlib
import importlib
import importlib.metadata as metadata
import inspect
import io
import platform
import sys
import time
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import pandas as pd

from hose_quant.config import AppSettings
from hose_quant.data.models import (
    AuditReport,
    CapabilityResult,
    CapabilityStatus,
    ErrorCategory,
    FrameInspection,
    PackageInspection,
    result_from_frame,
)
from hose_quant.logging import redact_value

TEST_SYMBOLS = ["VNINDEX", "FPT", "HPG", "VCB"]
DOCUMENTATION_SOURCES = [
    "https://vnstocks.com/docs",
    "https://vnstocks.com/docs/vnstock/du-lieu-thi-truong-market-data",
    "https://vnstocks.com/docs/vnstock/tra-cuu-thong-tin-tham-chieu-reference",
    "https://vnstocks.com/docs/vnstock/so-sanh-free-va-sponsor",
    "https://vnstocks.com/docs/tai-lieu/lich-su-phien-ban",
    "https://github.com/vnstock-hq/vnstock-agent-guide/blob/main/AGENTS.md",
]


def sanitize_error(exc: BaseException, secrets: list[str] | None = None) -> str:
    text = f"{type(exc).__name__}: {exc}"
    text = str(redact_value(text, secrets))
    return text[:500]


def categorize_exception(exc: BaseException) -> ErrorCategory:
    text = f"{type(exc).__name__}: {exc}".lower()
    if isinstance(exc, TimeoutError) or "timeout" in text or "timed out" in text:
        return ErrorCategory.TIMEOUT
    if isinstance(exc, ImportError) or "no module named" in text:
        return ErrorCategory.PACKAGE_NOT_INSTALLED
    if "rate limit" in text or "too many requests" in text or " 429" in text or "429" in text:
        return ErrorCategory.RATE_LIMIT
    if (
        "unauthorized" in text
        or "forbidden" in text
        or "invalid api" in text
        or "api key" in text
        or "401" in text
        or "403" in text
    ):
        return ErrorCategory.AUTHENTICATION
    if (
        "connection" in text
        or "network" in text
        or "name resolution" in text
        or "nodename nor servname" in text
        or "max retries" in text
        or "dns" in text
    ):
        return ErrorCategory.NETWORK
    return ErrorCategory.PROVIDER


def status_from_error(category: ErrorCategory) -> CapabilityStatus:
    return {
        ErrorCategory.AUTHENTICATION: CapabilityStatus.AUTHENTICATION_REQUIRED,
        ErrorCategory.RATE_LIMIT: CapabilityStatus.RATE_LIMITED,
        ErrorCategory.NETWORK: CapabilityStatus.NETWORK_ERROR,
        ErrorCategory.TIMEOUT: CapabilityStatus.NETWORK_ERROR,
        ErrorCategory.EMPTY_RESPONSE: CapabilityStatus.EMPTY_RESPONSE,
        ErrorCategory.INVALID_SCHEMA: CapabilityStatus.INVALID_SCHEMA,
        ErrorCategory.PACKAGE_NOT_INSTALLED: CapabilityStatus.UNAVAILABLE_PACKAGE,
    }.get(category, CapabilityStatus.PROVIDER_ERROR)


def inspect_dataframe(
    value: Any,
    *,
    required_columns: set[str] | None = None,
    timestamp_candidates: tuple[str, ...] = ("time", "date", "datetime", "tradingDate"),
) -> FrameInspection:
    if isinstance(value, pd.DataFrame):
        frame = value.copy()
    elif isinstance(value, list):
        frame = pd.DataFrame(value)
    elif isinstance(value, dict):
        frame = pd.DataFrame([value])
    else:
        return FrameInspection(
            data_quality_findings=[f"Response type {type(value).__name__} is not tabular."],
            error_category=ErrorCategory.INVALID_SCHEMA,
        )

    schema = {str(column): str(dtype) for column, dtype in frame.dtypes.items()}
    findings: list[str] = []
    category = ErrorCategory.NONE

    if frame.empty:
        return FrameInspection(
            row_count=0,
            schema_summary=schema,
            data_quality_findings=["Response contained no rows."],
            error_category=ErrorCategory.EMPTY_RESPONSE,
        )

    if required_columns:
        missing = sorted(required_columns - set(map(str, frame.columns)))
        if missing:
            findings.append(f"Missing required columns: {', '.join(missing)}.")
            category = ErrorCategory.INVALID_SCHEMA

    timestamp_column = next(
        (candidate for candidate in timestamp_candidates if candidate in frame.columns),
        None,
    )
    earliest = latest = timezone = None
    if timestamp_column is None:
        findings.append("No recognized timestamp column was present.")
    else:
        parsed = pd.to_datetime(frame[timestamp_column], errors="coerce")
        valid = parsed.dropna()
        if valid.empty:
            findings.append(f"Timestamp column {timestamp_column!r} could not be parsed.")
            category = ErrorCategory.INVALID_SCHEMA
        else:
            earliest = valid.min().isoformat()
            latest = valid.max().isoformat()
            timezone = str(getattr(valid.dt, "tz", None) or "naive/unspecified")
            if parsed.duplicated().any():
                findings.append(f"Duplicate timestamps detected in {timestamp_column!r}.")
            if not valid.is_monotonic_increasing:
                findings.append(f"Timestamps in {timestamp_column!r} are not sorted ascending.")

    null_columns = [str(column) for column in frame.columns if frame[column].isna().any()]
    if null_columns:
        findings.append(f"Null values detected in columns: {', '.join(null_columns)}.")

    return FrameInspection(
        row_count=len(frame),
        earliest_timestamp=earliest,
        latest_timestamp=latest,
        timezone_information=timezone,
        schema_summary=schema,
        data_quality_findings=findings,
        error_category=category,
    )


class VnstockCapabilityAuditor:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def audit_capabilities(self, *, live: bool) -> AuditReport:
        package = self.inspect_package()
        capabilities = self._documented_capabilities(package)
        auth_state = (
            "VNSTOCK_API_KEY set" if self.settings.vnstock_api_key else "VNSTOCK_API_KEY missing"
        )
        blocking_issues: list[str] = []

        if live:
            api_key = self.settings.require_vnstock_api_key()
            live_capabilities = self._run_live_checks(package, api_key)
            capabilities = self._merge_capabilities(capabilities, live_capabilities)
        else:
            blocking_issues.append(
                "Live vnstock audit was not executed because offline mode was used."
            )
            if not self.settings.vnstock_api_key:
                blocking_issues.append("VNSTOCK_API_KEY was not available in the Codex shell.")

        return AuditReport(
            operating_system=f"{platform.system()} {platform.release()}",
            python_version=sys.version.split()[0],
            package_inspection=package,
            authentication_state=auth_state,
            documentation_sources=DOCUMENTATION_SOURCES,
            tested_symbols=TEST_SYMBOLS,
            capabilities=capabilities,
            documented_free_tier_constraints=[
                "Community/free users are directed to the vnstock package only.",
                (
                    "Official comparison states community rate limits are very low "
                    "for automated systems."
                ),
                "Local package startup message observed community tier as 60 requests per minute.",
                (
                    "Sponsor documentation advertises higher limits and deeper data "
                    "for automated workflows."
                ),
            ],
            documented_rate_limit=(
                "Community package startup message observed 60 requests/minute; official "
                "comparison page describes free rate limits as very low but does not provide "
                "a full quota table."
            ),
            unresolved_uncertainties=[
                "No live API-key-backed requests were completed in this Phase 0 run.",
                "Historical point-in-time universe membership was not verified.",
                "Adjusted-price methodology and corporate-action completeness were not verified.",
                (
                    "Free-tier minute lookback, pagination behavior, and delay characteristics "
                    "were not verified."
                ),
                (
                    "WebSocket entitlement for the free tier was not found in the free-package "
                    "docs inspected."
                ),
            ],
            blocking_issues=blocking_issues,
            conclusions=self._conclusions(live=live),
            recommended_phase_1_scope=[
                (
                    "Run the live audit with VNSTOCK_API_KEY in a local shell and commit "
                    "refreshed reports if checks pass."
                ),
                "Design immutable raw-data storage and normalized point-in-time schemas.",
                (
                    "Define conservative polling limits for a small initial HOSE universe "
                    "after live latency/rate evidence."
                ),
                (
                    "Decide whether paid or alternative data is needed for minute bars, "
                    "point-in-time universe data, and corporate actions."
                ),
            ],
        )

    def inspect_package(self) -> PackageInspection:
        try:
            dist = metadata.distribution("vnstock")
            version = dist.version
            raw_python_requires = dist.metadata.json.get("requires_python")
            python_requires = (
                raw_python_requires if isinstance(raw_python_requires, str) else None
            )
        except metadata.PackageNotFoundError:
            return PackageInspection(
                package_version="not installed",
                python_requires=None,
                import_notes=["vnstock distribution was not installed."],
            )

        top_level_symbols: list[str] = []
        module_path: str | None = None
        providers = {"kbs", "vci", "msn"}
        notes: list[str] = []
        try:
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                vnstock = importlib.import_module("vnstock")
            module_path = str(getattr(vnstock, "__file__", "") or "")
            top_level_symbols = sorted(name for name in dir(vnstock) if not name.startswith("_"))[
                :100
            ]
            providers.update(self._providers_from_signatures(vnstock))
        except Exception as exc:  # pragma: no cover - depends on provider startup behavior.
            notes.append(f"Package import inspection failed: {sanitize_error(exc)}")

        return PackageInspection(
            package_version=version,
            python_requires=python_requires,
            module_path=module_path,
            top_level_symbols=top_level_symbols,
            supported_providers_found=sorted(providers),
            authentication_mechanism=(
                "Project live audit reads VNSTOCK_API_KEY; installed package exposes register_user "
                "and change_api_key helpers and also maintains local ~/.vnstock auth state."
            ),
            import_notes=notes,
        )

    def _providers_from_signatures(self, vnstock: Any) -> set[str]:
        providers: set[str] = set()
        for name in ("Quote", "Listing", "Company", "Finance", "Trading"):
            obj = getattr(vnstock, name, None)
            if obj is None:
                continue
            try:
                signature = str(inspect.signature(obj)).lower()
            except (TypeError, ValueError):
                continue
            for provider in ("kbs", "vci", "msn", "dnse", "fmp", "xno"):
                if provider in signature:
                    providers.add(provider)
        return providers

    def _documented_capabilities(self, package: PackageInspection) -> list[CapabilityResult]:
        version = package.package_version
        documented = [
            CapabilityResult(
                capability_name="daily historical OHLCV",
                status=CapabilityStatus.DOCUMENTED_NOT_TESTED,
                package_version=version,
                library_method="Market().equity(symbol).ohlcv(..., resolution='1D')",
                tested_symbols=["FPT", "HPG", "VCB"],
                evidence_notes=[
                    "Official Market docs document equity ohlcv with intervals including 1D.",
                    "Installed signature uses resolution='1D' and source='kbs'.",
                ],
                limitations=[
                    (
                        "Live data schema, adjusted/unadjusted status, and invalid-symbol "
                        "behavior are unverified."
                    )
                ],
            ),
            CapabilityResult(
                capability_name="historical intraday bars",
                status=CapabilityStatus.DOCUMENTED_NOT_TESTED,
                package_version=version,
                library_method="Market().equity(symbol).ohlcv(..., resolution='1m')",
                tested_symbols=["FPT"],
                evidence_notes=[
                    "Official Market docs list 1m, 5m, 15m, 30m, 1h, 1D, and 1W intervals.",
                    "Agent Guide notes intraday data is recent only.",
                ],
                limitations=[
                    (
                        "Free-tier lookback, delay, pagination, and practical polling "
                        "suitability are unverified."
                    ),
                ],
            ),
            CapabilityResult(
                capability_name="latest quote",
                status=CapabilityStatus.DOCUMENTED_NOT_TESTED,
                package_version=version,
                library_method="Market().equity(symbol).quote()",
                tested_symbols=["VCB"],
                evidence_notes=["Official Market docs document quote() for an equity symbol."],
            ),
            CapabilityResult(
                capability_name="batch price board",
                status=CapabilityStatus.DOCUMENTED_NOT_TESTED,
                package_version=version,
                library_method="Market().quote(['VCB', 'HPG', 'FPT'])",
                tested_symbols=["FPT", "HPG", "VCB"],
                evidence_notes=["Official Market docs document quote() with a list of symbols."],
                limitations=["Maximum symbols per request and latency are unverified."],
            ),
            CapabilityResult(
                capability_name="symbol metadata",
                status=CapabilityStatus.DOCUMENTED_NOT_TESTED,
                package_version=version,
                library_method="Reference().equity.list() / Reference().company(symbol).info()",
                tested_symbols=["FPT", "HPG", "VCB"],
                evidence_notes=[
                    "Official Reference docs document listed equity and company info methods."
                ],
            ),
            CapabilityResult(
                capability_name="current exchange universe",
                status=CapabilityStatus.DOCUMENTED_NOT_TESTED,
                package_version=version,
                library_method="Reference().equity.list_by_exchange()",
                tested_symbols=["HOSE"],
                evidence_notes=[
                    "Official Reference docs document list_by_exchange for HOSE, HNX, UPCOM."
                ],
                limitations=["Historical point-in-time universe membership is unverified."],
            ),
            CapabilityResult(
                capability_name="listing date and delisting status",
                status=CapabilityStatus.UNKNOWN,
                package_version=version,
                library_method="Reference().company(symbol).info()",
                tested_symbols=["FPT"],
                evidence_notes=[
                    "Company info is documented, but listing/delisting fields were not verified."
                ],
            ),
            CapabilityResult(
                capability_name="corporate actions",
                status=CapabilityStatus.DOCUMENTED_NOT_TESTED,
                package_version=version,
                library_method="Reference().company(symbol).events()",
                tested_symbols=["FPT"],
                evidence_notes=["Official Reference docs document company events."],
                limitations=[
                    "Ex-date, record-date, split, rights, and completeness fields are unverified."
                ],
            ),
            CapabilityResult(
                capability_name="adjusted prices",
                status=CapabilityStatus.UNKNOWN,
                package_version=version,
                library_method="Market().equity(symbol).ohlcv()",
                tested_symbols=["FPT"],
                evidence_notes=[
                    (
                        "Adjusted versus unadjusted price semantics were not verified "
                        "in docs or live data."
                    )
                ],
            ),
            CapabilityResult(
                capability_name="foreign trading",
                status=CapabilityStatus.UNAVAILABLE_FREE_TIER,
                package_version=version,
                library_method=None,
                evidence_notes=[
                    (
                        "Official free-vs-sponsor comparison lists foreign_flow as missing "
                        "from community Market.equity."
                    )
                ],
            ),
            CapabilityResult(
                capability_name="proprietary trading",
                status=CapabilityStatus.UNAVAILABLE_FREE_TIER,
                package_version=version,
                library_method=None,
                evidence_notes=[
                    (
                        "Official free-vs-sponsor comparison lists proprietary_flow as "
                        "missing from community Market.equity."
                    )
                ],
            ),
            CapabilityResult(
                capability_name="order-book depth",
                status=CapabilityStatus.UNAVAILABLE_FREE_TIER,
                package_version=version,
                library_method=None,
                evidence_notes=[
                    (
                        "Official free-vs-sponsor comparison lists order_book as missing "
                        "from community Market.equity."
                    ),
                    "Version history notes price_depth removal from free VCI quote.",
                ],
            ),
            CapabilityResult(
                capability_name="streaming or WebSocket",
                status=CapabilityStatus.UNAVAILABLE_PACKAGE,
                package_version=version,
                library_method=None,
                evidence_notes=[
                    (
                        "No documented WebSocket interface was found in the inspected "
                        "free-package docs."
                    ),
                    "Agent Guide lists production pipeline/streaming under sponsored libraries.",
                ],
            ),
            CapabilityResult(
                capability_name="rate limits",
                status=CapabilityStatus.DOCUMENTED_NOT_TESTED,
                package_version=version,
                library_method=None,
                evidence_notes=[
                    "Local package startup text observed community tier as 60 requests per minute.",
                    (
                        "Official comparison page states community rate limits are very low "
                        "for automated systems."
                    ),
                ],
                limitations=["No live response headers were observed in this run."],
            ),
        ]
        return documented

    def _run_live_checks(self, package: PackageInspection, api_key: str) -> list[CapabilityResult]:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            vnstock = importlib.import_module("vnstock")
            change_api_key = getattr(vnstock, "change_api_key", None)
            if callable(change_api_key):
                change_api_key(api_key)
            market_module = importlib.import_module("vnstock.ui")
            market = market_module.Market()
            reference = market_module.Reference()

        end = date.today()
        start = end - timedelta(days=45)
        checks: list[tuple[str, str, list[str], set[str] | None, Callable[[], Any]]] = [
            (
                "daily historical OHLCV",
                "Market().equity('FPT').ohlcv(..., resolution='1D')",
                ["FPT"],
                {"time", "open", "high", "low", "close", "volume"},
                lambda: market.equity("FPT").ohlcv(
                    start=start.isoformat(),
                    end=end.isoformat(),
                    resolution="1D",
                    count=100,
                    source="kbs",
                ),
            ),
            (
                "historical intraday bars",
                "Market().equity('FPT').ohlcv(..., resolution='1m')",
                ["FPT"],
                {"time", "open", "high", "low", "close", "volume"},
                lambda: market.equity("FPT").ohlcv(resolution="1m", count=5, source="kbs"),
            ),
            (
                "latest quote",
                "Market().equity('VCB').quote()",
                ["VCB"],
                None,
                lambda: market.equity("VCB").quote(source="kbs"),
            ),
            (
                "batch price board",
                "Market().quote(['VCB', 'HPG', 'FPT'])",
                ["VCB", "HPG", "FPT"],
                None,
                lambda: market.quote(["VCB", "HPG", "FPT"]),
            ),
            (
                "current exchange universe",
                "Reference().equity.list_by_exchange()",
                ["HOSE"],
                None,
                lambda: reference.equity.list_by_exchange(source="kbs"),
            ),
            (
                "corporate actions",
                "Reference().company('FPT').events()",
                ["FPT"],
                None,
                lambda: reference.company("FPT").events(source="kbs"),
            ),
        ]

        return [
            self._run_one_live_check(
                capability_name=name,
                method=method,
                symbols=symbols,
                required_columns=required_columns,
                func=func,
                package_version=package.package_version,
                api_key=api_key,
            )
            for name, method, symbols, required_columns, func in checks
        ]

    def _run_one_live_check(
        self,
        *,
        capability_name: str,
        method: str,
        symbols: list[str],
        required_columns: set[str] | None,
        func: Callable[[], Any],
        package_version: str,
        api_key: str,
    ) -> CapabilityResult:
        started = time.perf_counter()
        try:
            response = self._with_retries(func)
        except Exception as exc:
            category = categorize_exception(exc)
            return CapabilityResult(
                capability_name=capability_name,
                status=status_from_error(category),
                package_version=package_version,
                library_method=method,
                tested_symbols=symbols,
                elapsed_latency_ms=round((time.perf_counter() - started) * 1000, 2),
                error_category=category,
                sanitized_error_message=sanitize_error(exc, [api_key]),
            )
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        frame = inspect_dataframe(response, required_columns=required_columns)
        status = CapabilityStatus.VERIFIED
        if frame.error_category is ErrorCategory.EMPTY_RESPONSE:
            status = CapabilityStatus.EMPTY_RESPONSE
        elif frame.error_category is ErrorCategory.INVALID_SCHEMA:
            status = CapabilityStatus.INVALID_SCHEMA
        return result_from_frame(
            capability_name=capability_name,
            status=status,
            package_version=package_version,
            library_method=method,
            tested_symbols=symbols,
            frame=frame,
            elapsed_latency_ms=elapsed,
            evidence_notes=["Live request completed in the local environment."],
        )

    def _with_retries(self, func: Callable[[], Any]) -> Any:
        last_exc: BaseException | None = None
        for attempt in range(1, self.settings.max_retry_attempts + 1):
            try:
                return func()
            except Exception as exc:  # pragma: no cover - live-provider dependent.
                last_exc = exc
                category = categorize_exception(exc)
                if category in {
                    ErrorCategory.AUTHENTICATION,
                    ErrorCategory.INVALID_SCHEMA,
                    ErrorCategory.EMPTY_RESPONSE,
                }:
                    raise
                if attempt >= self.settings.max_retry_attempts:
                    raise
                time.sleep(min(2 ** (attempt - 1), 4))
        if last_exc:
            raise last_exc
        msg = "Provider call did not execute."
        raise RuntimeError(msg)

    def _merge_capabilities(
        self,
        documented: list[CapabilityResult],
        live: list[CapabilityResult],
    ) -> list[CapabilityResult]:
        live_by_name = {item.capability_name: item for item in live}
        merged = [live_by_name.get(item.capability_name, item) for item in documented]
        known = {item.capability_name for item in merged}
        merged.extend(item for item in live if item.capability_name not in known)
        return merged

    def _conclusions(self, *, live: bool) -> dict[str, str]:
        suffix = "" if live else " Not live-verified in this Phase 0 run."
        return {
            "daily_ohlcv_usable": (
                "Documented and package-exposed; require live schema validation before "
                "production use."
            )
            + suffix,
            "historical_minute_data_usable": (
                "Documented at 1m resolution, but free-tier lookback/delay/pagination "
                "remain unverified."
            )
            + suffix,
            "free_tier_minute_polling_practical": (
                "Unverified; assume not practical beyond a very small universe until "
                "live rate/latency evidence exists."
            ),
            "batch_price_board_polling_practical": (
                "Documented for multiple symbols; maximum batch size and latency are unverified."
            ),
            "websocket_free_tier_available": "No free-tier WebSocket interface was verified.",
            "recommended_maximum_initial_live_universe": (
                "20 symbols until live audit proves latency and rate-limit headroom."
            ),
            "recommended_polling_frequency": (
                "At most once per minute for initial experiments; reduce if rate-limit "
                "signals appear."
            ),
            "alternative_or_paid_source_needed": (
                "Likely needed if Phase 1 requires robust minute histories, streaming, "
                "order book, or point-in-time corporate-action data."
            ),
        }
