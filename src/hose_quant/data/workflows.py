from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from hose_quant.config import AppSettings
from hose_quant.data.manifests import build_manifest, create_run_id, write_manifest
from hose_quant.data.models import (
    ValidationResult,
    ValidationSeverity,
    WorkflowResult,
    utc_now,
)
from hose_quant.data.normalizers import (
    normalize_daily_ohlcv,
    normalize_intraday_bars,
    normalize_quote_snapshot,
    normalize_universe_snapshot,
)
from hose_quant.data.storage import DataStorage
from hose_quant.data.validators import (
    has_blocking_errors,
    validate_daily_ohlcv,
    validate_intraday_bars,
    validate_quote_snapshot,
    validate_universe_snapshot,
    write_validation_reports,
)
from hose_quant.data.vnstock_adapter import VnstockDataProvider, sanitize_error


class SafetyLimitError(ValueError):
    """Raised when a command exceeds the configured free-tier safety limits."""


class DataWorkflow:
    def __init__(
        self,
        settings: AppSettings,
        *,
        provider: VnstockDataProvider | None = None,
        storage: DataStorage | None = None,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.storage = storage or DataStorage(settings.data_dir)
        self.storage.ensure_layout()

    def fetch_universe(
        self,
        *,
        exchange: str,
        dry_run: bool = False,
    ) -> WorkflowResult:
        started = utc_now()
        run_id = create_run_id("fetch-universe", started)
        if dry_run:
            return self._dry_run_result(
                run_id=run_id,
                command="data fetch-universe",
                started=started,
                exchange=exchange,
            )
        provider = self._require_provider()
        validation_results: list[ValidationResult] = []
        output_paths = []
        errors: list[str] = []
        raw_rows = normalized_rows = 0
        try:
            raw = provider.fetch_universe(exchange)
            raw_rows = len(raw)
            output_paths.append(self.storage.write_raw_frame("universe", run_id, raw))
            normalized, diagnostics = normalize_universe_snapshot(raw, exchange=exchange)
            normalized_rows = len(normalized)
            validation_results = validate_universe_snapshot(normalized, diagnostics)
            if not has_blocking_errors(validation_results):
                snapshot_date = started.date()
                output_paths.append(
                    self.storage.write_parquet(
                        normalized,
                        self.storage.normalized_universe_path(snapshot_date, run_id),
                    )
                )
        except Exception as exc:
            errors.append(sanitize_error(exc))
        status = _status(validation_results, errors)
        manifest = build_manifest(
            run_id=run_id,
            command="data fetch-universe",
            started_at_utc=started,
            finished_at_utc=utc_now(),
            status=status,
            exchange=exchange,
            row_counts={"raw": raw_rows, "normalized": normalized_rows},
            output_paths=output_paths,
            validation_results=validation_results,
            error_summary=errors,
            provider_call_count=provider.call_count,
        )
        manifest_path = write_manifest(manifest, self.storage.manifest_root)
        return WorkflowResult(
            manifest=manifest,
            validation_results=validation_results,
            manifest_path=str(manifest_path),
        )

    def backfill_daily(
        self,
        *,
        symbols: list[str],
        start: date,
        end: date,
        allow_large_universe: bool = False,
        dry_run: bool = False,
    ) -> WorkflowResult:
        clean_symbols = _clean_symbols(symbols)
        self._enforce_symbol_limit(clean_symbols, allow_large_universe=allow_large_universe)
        started = utc_now()
        run_id = create_run_id("backfill-daily", started)
        if dry_run:
            return self._dry_run_result(
                run_id=run_id,
                command="data backfill-daily",
                started=started,
                symbols=clean_symbols,
                start_date=start.isoformat(),
                end_date=end.isoformat(),
            )
        provider = self._require_provider()
        validation_results: list[ValidationResult] = []
        output_paths = []
        errors: list[str] = []
        raw_frames: list[pd.DataFrame] = []
        normalized_frames: list[pd.DataFrame] = []
        for symbol in clean_symbols:
            try:
                raw = provider.fetch_daily_ohlcv(symbol, start, end)
                raw_with_symbol = raw.copy()
                raw_with_symbol["symbol"] = symbol
                raw_frames.append(raw_with_symbol)
                normalized_frames.append(normalize_daily_ohlcv(raw, symbol=symbol, exchange="HOSE"))
            except Exception as exc:
                errors.append(f"{symbol}: {sanitize_error(exc)}")
        if raw_frames:
            raw_all = pd.concat(raw_frames, ignore_index=True)
            output_paths.append(self.storage.write_raw_frame("daily", run_id, raw_all))
        normalized_all = (
            pd.concat(normalized_frames, ignore_index=True) if normalized_frames else pd.DataFrame()
        )
        if not normalized_all.empty:
            validation_results = validate_daily_ohlcv(normalized_all)
            if not has_blocking_errors(validation_results):
                output_paths.extend(self.storage.write_daily_partitions(normalized_all, run_id))
        status = _status(validation_results, errors)
        manifest = build_manifest(
            run_id=run_id,
            command="data backfill-daily",
            started_at_utc=started,
            finished_at_utc=utc_now(),
            status=status,
            symbols=clean_symbols,
            exchange="HOSE",
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            row_counts={
                "raw": sum(len(frame) for frame in raw_frames),
                "normalized": len(normalized_all),
            },
            output_paths=output_paths,
            validation_results=validation_results,
            error_summary=errors,
            provider_call_count=provider.call_count,
        )
        manifest_path = write_manifest(manifest, self.storage.manifest_root)
        return WorkflowResult(
            manifest=manifest,
            validation_results=validation_results,
            manifest_path=str(manifest_path),
        )

    def fetch_intraday(
        self,
        *,
        symbols: list[str],
        resolution: str,
        lookback_days: int,
        allow_large_universe: bool = False,
        dry_run: bool = False,
    ) -> WorkflowResult:
        clean_symbols = _clean_symbols(symbols)
        self._enforce_symbol_limit(clean_symbols, allow_large_universe=allow_large_universe)
        started = utc_now()
        run_id = create_run_id("fetch-intraday", started)
        if dry_run:
            return self._dry_run_result(
                run_id=run_id,
                command="data fetch-intraday",
                started=started,
                symbols=clean_symbols,
                resolution=resolution,
            )
        provider = self._require_provider()
        validation_results: list[ValidationResult] = []
        output_paths = []
        errors: list[str] = []
        raw_frames: list[pd.DataFrame] = []
        normalized_frames: list[pd.DataFrame] = []
        for symbol in clean_symbols:
            try:
                raw = provider.fetch_intraday_bars(symbol, resolution, lookback_days)
                raw_with_symbol = raw.copy()
                raw_with_symbol["symbol"] = symbol
                raw_frames.append(raw_with_symbol)
                normalized_frames.append(
                    normalize_intraday_bars(
                        raw, symbol=symbol, resolution=resolution, exchange="HOSE"
                    )
                )
            except Exception as exc:
                errors.append(f"{symbol}: {sanitize_error(exc)}")
        if raw_frames:
            raw_all = pd.concat(raw_frames, ignore_index=True)
            output_paths.append(self.storage.write_raw_frame("intraday", run_id, raw_all))
        normalized_all = (
            pd.concat(normalized_frames, ignore_index=True) if normalized_frames else pd.DataFrame()
        )
        if not normalized_all.empty:
            validation_results = validate_intraday_bars(normalized_all)
            if not has_blocking_errors(validation_results):
                output_paths.extend(
                    self.storage.write_intraday_partitions(
                        normalized_all,
                        resolution=resolution,
                        run_id=run_id,
                    )
                )
        status = _status(validation_results, errors)
        manifest = build_manifest(
            run_id=run_id,
            command="data fetch-intraday",
            started_at_utc=started,
            finished_at_utc=utc_now(),
            status=status,
            symbols=clean_symbols,
            exchange="HOSE",
            resolution=resolution,
            row_counts={
                "raw": sum(len(frame) for frame in raw_frames),
                "normalized": len(normalized_all),
            },
            output_paths=output_paths,
            validation_results=validation_results,
            error_summary=errors,
            provider_call_count=provider.call_count,
        )
        manifest_path = write_manifest(manifest, self.storage.manifest_root)
        return WorkflowResult(
            manifest=manifest,
            validation_results=validation_results,
            manifest_path=str(manifest_path),
        )

    def snapshot_quotes(
        self,
        *,
        symbols: list[str],
        allow_large_universe: bool = False,
        dry_run: bool = False,
    ) -> WorkflowResult:
        clean_symbols = _clean_symbols(symbols)
        self._enforce_symbol_limit(clean_symbols, allow_large_universe=allow_large_universe)
        started = utc_now()
        run_id = create_run_id("snapshot-quotes", started)
        if dry_run:
            return self._dry_run_result(
                run_id=run_id,
                command="data snapshot-quotes",
                started=started,
                symbols=clean_symbols,
            )
        provider = self._require_provider()
        validation_results: list[ValidationResult] = []
        output_paths = []
        errors: list[str] = []
        raw_rows = normalized_rows = 0
        try:
            raw = provider.fetch_quote_snapshot(clean_symbols)
            raw_rows = len(raw)
            output_paths.append(self.storage.write_raw_frame("quotes", run_id, raw))
            normalized, missing = normalize_quote_snapshot(raw, requested_symbols=clean_symbols)
            normalized_rows = len(normalized)
            validation_results = validate_quote_snapshot(
                normalized,
                requested_symbols=clean_symbols,
                missing_symbols=missing,
            )
            if not has_blocking_errors(validation_results):
                output_paths.append(
                    self.storage.write_parquet(
                        normalized,
                        self.storage.normalized_quotes_path(started.date(), run_id),
                    )
                )
        except Exception as exc:
            errors.append(sanitize_error(exc))
        status = _status(validation_results, errors)
        manifest = build_manifest(
            run_id=run_id,
            command="data snapshot-quotes",
            started_at_utc=started,
            finished_at_utc=utc_now(),
            status=status,
            symbols=clean_symbols,
            exchange="HOSE",
            row_counts={"raw": raw_rows, "normalized": normalized_rows},
            output_paths=output_paths,
            validation_results=validation_results,
            error_summary=errors,
            provider_call_count=provider.call_count,
        )
        manifest_path = write_manifest(manifest, self.storage.manifest_root)
        return WorkflowResult(
            manifest=manifest,
            validation_results=validation_results,
            manifest_path=str(manifest_path),
        )

    def validate_existing_data(self, *, write_reports: bool = True) -> WorkflowResult:
        started = utc_now()
        run_id = create_run_id("validate", started)
        validation_results: list[ValidationResult] = []
        for dataset, validator in [
            ("daily", validate_daily_ohlcv),
            ("intraday", validate_intraday_bars),
        ]:
            frame = self.storage.read_normalized_dataset(dataset)
            if frame is not None:
                validation_results.extend(validator(frame))
        quote_frame = self.storage.read_normalized_dataset("quotes")
        if quote_frame is not None:
            validation_results.extend(
                validate_quote_snapshot(
                    quote_frame,
                    requested_symbols=sorted(quote_frame["symbol"].dropna().unique().tolist()),
                )
            )
        if not validation_results:
            validation_results.append(
                ValidationResult(
                    dataset_name="all",
                    severity=ValidationSeverity.INFO,
                    check_name="no_normalized_data",
                    message="No normalized datasets were found to validate.",
                )
            )
        output_paths = []
        if write_reports:
            json_path, markdown_path = write_validation_reports(
                validation_results,
                json_path=self.settings.report_dir / "data_quality" / "latest.json",
                markdown_path=self.settings.report_dir / "data_quality" / "latest.md",
            )
            output_paths.extend([json_path, markdown_path])
        manifest = build_manifest(
            run_id=run_id,
            command="data validate",
            started_at_utc=started,
            finished_at_utc=utc_now(),
            status="failed" if has_blocking_errors(validation_results) else "success",
            output_paths=output_paths,
            validation_results=validation_results,
            dry_run=not write_reports,
        )
        manifest_path = write_manifest(manifest, self.storage.manifest_root)
        return WorkflowResult(
            manifest=manifest,
            validation_results=validation_results,
            manifest_path=str(manifest_path),
        )

    def _dry_run_result(
        self,
        *,
        run_id: str,
        command: str,
        started: datetime,
        symbols: list[str] | None = None,
        exchange: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        resolution: str | None = None,
    ) -> WorkflowResult:
        manifest = build_manifest(
            run_id=run_id,
            command=command,
            started_at_utc=started,
            finished_at_utc=utc_now(),
            status="dry_run",
            symbols=symbols,
            exchange=exchange,
            start_date=start_date,
            end_date=end_date,
            resolution=resolution,
            dry_run=True,
        )
        return WorkflowResult(manifest=manifest)

    def _require_provider(self) -> VnstockDataProvider:
        if self.provider is None:
            self.provider = VnstockDataProvider(self.settings)
        return self.provider

    def _enforce_symbol_limit(
        self,
        symbols: list[str],
        *,
        allow_large_universe: bool,
    ) -> None:
        if len(symbols) > self.settings.max_quote_symbols and not allow_large_universe:
            msg = (
                f"Requested {len(symbols)} symbols, above safe default "
                f"{self.settings.max_quote_symbols}. Re-run with --allow-large-universe "
                "only after confirming quota impact."
            )
            raise SafetyLimitError(msg)


def _clean_symbols(symbols: list[str]) -> list[str]:
    cleaned = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
    if not cleaned:
        raise ValueError("At least one symbol is required.")
    return cleaned


def _status(validation_results: list[ValidationResult], errors: list[str]) -> str:
    if errors or has_blocking_errors(validation_results):
        return "failed"
    return "success"
