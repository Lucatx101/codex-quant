from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from hose_quant.config import AppSettings
from hose_quant.data.contracts import (
    AVAILABILITY_CONTRACT_VERSION,
    DAILY_COVERAGE_CONTRACT_VERSION,
    DAILY_PANEL_CONTRACT_VERSION,
    LIQUIDITY_CONTRACT_VERSION,
    UNIVERSE_CONTRACT_VERSION,
)
from hose_quant.data.coverage import (
    KNOWN_COVERAGE_RISKS,
    summarize_daily_coverage,
    write_daily_coverage_report,
)
from hose_quant.data.coverage import (
    audit_daily_coverage as build_daily_coverage_audit,
)
from hose_quant.data.feature_inputs import (
    apply_liquidity_to_universe,
    characterize_liquidity,
    daily_availability_diagnostics,
    prepare_research_universe,
    write_availability_report,
)
from hose_quant.data.feature_inputs import (
    build_daily_panel as build_feature_daily_panel,
)
from hose_quant.data.manifests import build_manifest, create_run_id, write_manifest
from hose_quant.data.market_time import aware_timestamp_to_utc
from hose_quant.data.models import (
    DailyCoverageConfig,
    LiquidityScreenConfig,
    LiquidityUnitPolicy,
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
from hose_quant.data.unit_provenance import resolve_daily_unit_policy
from hose_quant.data.validators import (
    has_blocking_errors,
    validate_availability_diagnostics,
    validate_daily_coverage,
    validate_daily_ohlcv,
    validate_daily_panel,
    validate_intraday_bars,
    validate_liquidity_characterization,
    validate_quote_snapshot,
    validate_research_universe,
    validate_universe_snapshot,
    write_validation_reports,
)
from hose_quant.data.vnstock_adapter import (
    DAILY_OHLCV_MAX_BARS_PER_REQUEST,
    VnstockDataProvider,
    sanitize_error,
)


class SafetyLimitError(ValueError):
    """Raised when a command exceeds the configured free-tier safety limits."""


MAX_SAFE_DAILY_CHUNK_CALENDAR_DAYS = 1095


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
            errors.append(self._sanitize_provider_error(exc))
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
        chunk_calendar_days: int | None = None,
        allow_large_universe: bool = False,
        dry_run: bool = False,
    ) -> WorkflowResult:
        clean_symbols = _clean_symbols(symbols)
        self._enforce_symbol_limit(clean_symbols, allow_large_universe=allow_large_universe)
        chunk_days = (
            self.settings.daily_backfill_chunk_calendar_days
            if chunk_calendar_days is None
            else chunk_calendar_days
        )
        chunks = daily_date_chunks(start, end, chunk_calendar_days=chunk_days)
        projected_call_count = len(clean_symbols) * len(chunks)
        self._enforce_provider_call_limit(
            projected_call_count,
            allow_large_universe=allow_large_universe,
        )
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
                parameters={
                    "chunk_calendar_days": chunk_days,
                    "chunk_count_per_symbol": len(chunks),
                    "projected_provider_call_count": projected_call_count,
                    "max_retry_attempts": self.settings.max_retry_attempts,
                    "provider_call_limit": self.settings.max_live_provider_calls,
                    "provider_sleep_seconds": self.settings.provider_sleep_seconds,
                },
            )
        provider = self._require_provider()
        source_unit_provenance = provider.daily_unit_provenance()
        validation_results: list[ValidationResult] = []
        output_paths = []
        errors: list[str] = []
        raw_frames: list[pd.DataFrame] = []
        normalized_frames: list[pd.DataFrame] = []
        successful_chunk_count = 0
        empty_chunk_count = 0
        abort_run = False
        for symbol in clean_symbols:
            for chunk_start, chunk_end in chunks:
                try:
                    raw = provider.fetch_daily_ohlcv(symbol, chunk_start, chunk_end)
                    raw_with_symbol = raw.copy()
                    raw_with_symbol["symbol"] = symbol
                    raw_with_symbol["request_start_date"] = chunk_start.isoformat()
                    raw_with_symbol["request_end_date"] = chunk_end.isoformat()
                    raw_frames.append(raw_with_symbol)
                    if len(raw) >= DAILY_OHLCV_MAX_BARS_PER_REQUEST:
                        raise ValueError(
                            "Daily provider response reached the 1,000-bar safety boundary; "
                            "reduce --chunk-calendar-days before trusting coverage."
                        )
                    normalized = normalize_daily_ohlcv(
                        raw,
                        symbol=symbol,
                        exchange="HOSE",
                        unit_provenance=source_unit_provenance,
                    )
                    _validate_daily_chunk_bounds(
                        normalized,
                        symbol=symbol,
                        start=chunk_start,
                        end=chunk_end,
                    )
                    if normalized.empty:
                        empty_chunk_count += 1
                    else:
                        normalized_frames.append(normalized)
                    successful_chunk_count += 1
                except Exception as exc:
                    errors.append(
                        f"{symbol} {chunk_start.isoformat()}..{chunk_end.isoformat()}: "
                        f"{self._sanitize_provider_error(exc)}"
                    )
                    abort_run = True
                    break
            if abort_run:
                break
        if raw_frames:
            raw_all = pd.concat(raw_frames, ignore_index=True)
            output_paths.append(self.storage.write_raw_frame("daily", run_id, raw_all))
        normalized_all = (
            pd.concat(normalized_frames, ignore_index=True) if normalized_frames else pd.DataFrame()
        )
        if not normalized_all.empty:
            validation_results = validate_daily_ohlcv(normalized_all)
            if not errors and not has_blocking_errors(validation_results):
                output_paths.extend(self.storage.write_daily_partitions(normalized_all, run_id))
        else:
            validation_results.append(
                ValidationResult(
                    dataset_name="daily",
                    severity=ValidationSeverity.ERROR,
                    check_name="daily_observations_available",
                    message="The provider returned no normalized daily observations.",
                    blocks_output=True,
                )
            )
        status = _status(validation_results, errors)
        effective_unit_policy = resolve_daily_unit_policy(normalized_all)
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
                "chunks_projected": projected_call_count,
                "chunks_succeeded": successful_chunk_count,
                "chunks_empty": empty_chunk_count,
            },
            output_paths=output_paths,
            parameters={
                "chunk_calendar_days": chunk_days,
                "chunk_count_per_symbol": len(chunks),
                "projected_provider_call_count": projected_call_count,
                "provider_call_limit": self.settings.max_live_provider_calls,
                "provider_sleep_seconds": self.settings.provider_sleep_seconds,
                "max_retry_attempts": self.settings.max_retry_attempts,
            },
            unit_provenance=effective_unit_policy,
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
                errors.append(f"{symbol}: {self._sanitize_provider_error(exc)}")
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
            errors.append(self._sanitize_provider_error(exc))
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

    def prepare_universe(
        self,
        *,
        exchange: str,
        snapshot_date: date | None,
        requested_reference_date: date | None,
        with_liquidity: bool,
        liquidity_reference_date: date | None,
        liquidity_config: LiquidityScreenConfig,
        daily_run_id: str | None = None,
    ) -> WorkflowResult:
        started = utc_now()
        run_id = create_run_id("prepare-universe", started)
        universe_source = self.storage.read_normalized_dataset_with_provenance("universe")
        if universe_source is None:
            return self._failed_local_result(
                run_id=run_id,
                command="data prepare-universe",
                started=started,
                validation_result=ValidationResult(
                    dataset_name="research_universe",
                    severity=ValidationSeverity.ERROR,
                    check_name="normalized_universe_available",
                    message="No local normalized universe snapshots were found.",
                    blocks_output=True,
                ),
                parameters={"exchange": exchange, "snapshot_date": snapshot_date},
                contract_versions={"research_universe": UNIVERSE_CONTRACT_VERSION},
            )

        universe_all, _universe_paths = universe_source
        observed = pd.Series(
            [
                aware_timestamp_to_utc(value, provider="vnstock")
                for value in universe_all["snapshot_timestamp_utc"]
            ],
            index=universe_all.index,
            dtype="datetime64[ns, UTC]",
        )
        if snapshot_date is not None:
            snapshot_candidates = universe_all[observed.dt.date == snapshot_date].copy()
        else:
            snapshot_candidates = universe_all[observed.notna()].copy()
        if snapshot_candidates.empty:
            target = snapshot_date.isoformat() if snapshot_date else "any valid date"
            return self._failed_local_result(
                run_id=run_id,
                command="data prepare-universe",
                started=started,
                validation_result=ValidationResult(
                    dataset_name="research_universe",
                    severity=ValidationSeverity.ERROR,
                    check_name="requested_snapshot_available",
                    message=f"No normalized universe snapshot was found for {target}.",
                    blocks_output=True,
                ),
                parameters={"exchange": exchange, "snapshot_date": snapshot_date},
                contract_versions={"research_universe": UNIVERSE_CONTRACT_VERSION},
            )
        candidate_times = pd.Series(
            [
                aware_timestamp_to_utc(value, provider="vnstock")
                for value in snapshot_candidates["snapshot_timestamp_utc"]
            ],
            index=snapshot_candidates.index,
            dtype="datetime64[ns, UTC]",
        )
        selected_timestamp = candidate_times.max()
        selected = snapshot_candidates[candidate_times == selected_timestamp].copy()
        selected_paths = _input_paths(selected)
        selected = selected.drop(columns=["__input_path"], errors="ignore")
        selected_snapshot_date = pd.Timestamp(selected_timestamp).date()
        prepared = prepare_research_universe(
            selected,
            exchange=exchange,
            requested_reference_date=requested_reference_date,
        )
        validation_results = validate_research_universe(
            prepared,
            expected_input_row_count=len(selected),
        )
        output_paths: list[Path] = []
        input_paths = list(selected_paths)
        reference_date = (
            liquidity_reference_date or requested_reference_date or selected_snapshot_date
        )
        unit_policy: LiquidityUnitPolicy | None = None
        if with_liquidity:
            if reference_date > selected_snapshot_date:
                raise ValueError(
                    "Liquidity reference date cannot be after the selected universe snapshot "
                    "observation date."
                )
            daily_source = self.storage.read_normalized_dataset_with_provenance(
                "daily",
                run_id=daily_run_id,
            )
            daily_all = (
                daily_source[0]
                if daily_source is not None
                else pd.DataFrame(
                    columns=[
                        "provider",
                        "symbol",
                        "exchange",
                        "date",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "adjusted_flag",
                        "source_resolution",
                        "ingestion_timestamp_utc",
                    ]
                )
            )
            candidate_symbols = sorted(
                prepared.loc[
                    (prepared["candidate_status"] == "included_candidate")
                    & prepared["symbol"].notna(),
                    "symbol",
                ]
                .astype(str)
                .unique()
                .tolist()
            )
            window_start = pd.bdate_range(
                end=pd.Timestamp(reference_date),
                periods=liquidity_config.window_weekdays,
            )[0].date()
            intermediate_panel = build_feature_daily_panel(
                daily_all,
                symbols=candidate_symbols,
                start=window_start,
                end=reference_date,
            )
            unit_policy = resolve_daily_unit_policy(intermediate_panel)
            liquidity_source_rows = _select_daily_source_rows(
                daily_all,
                symbols=candidate_symbols,
                start=window_start,
                end=reference_date,
            )
            panel_results = validate_daily_panel(
                intermediate_panel,
                expected_source_row_count=len(liquidity_source_rows),
            )
            validation_results.extend(panel_results)
            if daily_source is not None:
                input_paths.extend(_input_paths(liquidity_source_rows))
            if not has_blocking_errors(panel_results):
                liquidity = characterize_liquidity(
                    intermediate_panel,
                    symbols=candidate_symbols,
                    reference_date=reference_date,
                    config=liquidity_config,
                )
                liquidity_results = validate_liquidity_characterization(liquidity)
                validation_results.extend(liquidity_results)
                if not has_blocking_errors(liquidity_results):
                    prepared = apply_liquidity_to_universe(prepared, liquidity)
                    output_paths.append(
                        self.storage.write_parquet(
                            liquidity,
                            self.storage.feature_liquidity_path(reference_date, run_id),
                        )
                    )

        if not has_blocking_errors(validation_results):
            output_paths.append(
                self.storage.write_parquet(
                    prepared,
                    self.storage.feature_universe_path(selected_snapshot_date, run_id),
                )
            )
        status = "failed" if has_blocking_errors(validation_results) else "success"
        parameters = {
            "exchange": exchange.upper(),
            "selected_snapshot_observed_at_utc": selected_timestamp.isoformat(),
            "requested_reference_date": (
                requested_reference_date.isoformat() if requested_reference_date else None
            ),
            "with_liquidity": with_liquidity,
            "liquidity_reference_date": reference_date.isoformat() if with_liquidity else None,
            "liquidity_config": liquidity_config.model_dump(mode="json"),
            "daily_run_id": daily_run_id,
        }
        manifest = build_manifest(
            run_id=run_id,
            command="data prepare-universe",
            started_at_utc=started,
            finished_at_utc=utc_now(),
            status=status,
            exchange=exchange.upper(),
            row_counts={"selected_normalized": len(selected), "prepared": len(prepared)},
            input_paths=sorted(set(input_paths)),
            output_paths=output_paths,
            parameters=parameters,
            unit_provenance=unit_policy,
            data_contract_versions={
                "research_universe": UNIVERSE_CONTRACT_VERSION,
                **({"liquidity": LIQUIDITY_CONTRACT_VERSION} if with_liquidity else {}),
            },
            notes=[
                "Historical universe membership is not verified.",
                "Included rows are research candidates, not confirmed active/tradable listings.",
                "Liquidity unit verification is derived from selected daily-row provenance.",
            ],
            validation_results=validation_results,
        )
        manifest_path = write_manifest(manifest, self.storage.manifest_root)
        return WorkflowResult(
            manifest=manifest,
            validation_results=validation_results,
            manifest_path=str(manifest_path),
        )

    def build_daily_panel(
        self,
        *,
        symbols: list[str] | None,
        start: date,
        end: date,
        daily_run_id: str | None = None,
    ) -> WorkflowResult:
        started = utc_now()
        run_id = create_run_id("build-daily-panel", started)
        daily_source = self.storage.read_normalized_dataset_with_provenance(
            "daily",
            run_id=daily_run_id,
        )
        if daily_source is None:
            daily_all = pd.DataFrame(
                columns=[
                    "provider",
                    "symbol",
                    "exchange",
                    "date",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "adjusted_flag",
                    "source_resolution",
                    "ingestion_timestamp_utc",
                ]
            )
        else:
            daily_all = daily_source[0]
        selected_symbols = _clean_optional_symbols(symbols, daily_all)
        panel = build_feature_daily_panel(
            daily_all,
            symbols=selected_symbols,
            start=start,
            end=end,
        )
        unit_policy = resolve_daily_unit_policy(panel)
        selected_source = _select_daily_source_rows(
            daily_all,
            symbols=selected_symbols,
            start=start,
            end=end,
        )
        validation_results = validate_daily_panel(
            panel,
            expected_source_row_count=len(selected_source),
        )
        diagnostics = daily_availability_diagnostics(
            panel,
            symbols=selected_symbols,
            start=start,
            end=end,
        )
        validation_results.extend(validate_availability_diagnostics(diagnostics))
        if panel.empty:
            validation_results.append(
                ValidationResult(
                    dataset_name="daily_panel",
                    severity=ValidationSeverity.ERROR,
                    check_name="daily_observations_available",
                    message="No normalized daily rows matched the requested symbols/date range.",
                    blocks_output=True,
                )
            )

        output_paths: list[Path] = []
        diagnostics_path = self.storage.write_parquet(
            diagnostics,
            self.storage.feature_availability_path(start, end, run_id),
        )
        output_paths.append(diagnostics_path)
        report_json, report_markdown = write_availability_report(
            diagnostics,
            json_path=self.settings.report_dir / "feature_inputs" / f"{run_id}-availability.json",
            markdown_path=self.settings.report_dir / "feature_inputs" / f"{run_id}-availability.md",
        )
        output_paths.extend([report_json, report_markdown])
        if not has_blocking_errors(validation_results):
            output_paths.insert(
                0,
                self.storage.write_parquet(
                    panel,
                    self.storage.feature_daily_panel_path(start, end, run_id),
                ),
            )
        status = "failed" if has_blocking_errors(validation_results) else "success"
        input_paths = _input_paths(selected_source)
        manifest = build_manifest(
            run_id=run_id,
            command="data build-daily-panel",
            started_at_utc=started,
            finished_at_utc=utc_now(),
            status=status,
            symbols=selected_symbols,
            exchange="HOSE",
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            row_counts={
                "selected_normalized": len(selected_source),
                "panel": len(panel),
                "diagnostic_symbols": len(diagnostics),
            },
            input_paths=input_paths,
            output_paths=output_paths,
            unit_provenance=unit_policy,
            parameters={"daily_run_id": daily_run_id},
            data_contract_versions={
                "daily_panel": DAILY_PANEL_CONTRACT_VERSION,
                "availability": AVAILABILITY_CONTRACT_VERSION,
            },
            notes=[
                "No bars were synthesized or forward-filled.",
                "Weekday coverage does not remove Vietnamese holidays or exchange closures.",
                "Price adjustment status remains explicit and may be unknown.",
            ],
            validation_results=validation_results,
        )
        manifest_path = write_manifest(manifest, self.storage.manifest_root)
        return WorkflowResult(
            manifest=manifest,
            validation_results=validation_results,
            manifest_path=str(manifest_path),
        )

    def audit_daily_coverage(
        self,
        *,
        daily_run_id: str,
        start: date,
        end: date,
        snapshot_date: date | None,
        config: DailyCoverageConfig,
    ) -> WorkflowResult:
        started = utc_now()
        run_id = create_run_id("audit-daily-coverage", started)
        source_manifest = self.storage.read_manifest(daily_run_id)
        source_parameters: dict[str, object] = {
            "daily_run_id": daily_run_id,
            "snapshot_date": snapshot_date.isoformat() if snapshot_date else None,
            "coverage_config": config.model_dump(mode="json"),
        }
        if source_manifest is None:
            return self._failed_local_result(
                run_id=run_id,
                command="data audit-daily-coverage",
                started=started,
                validation_result=ValidationResult(
                    dataset_name="daily_coverage",
                    severity=ValidationSeverity.ERROR,
                    check_name="daily_source_manifest_available",
                    message=f"No ingestion manifest was found for daily run {daily_run_id}.",
                    blocks_output=True,
                ),
                parameters=source_parameters,
                contract_versions={"daily_coverage": DAILY_COVERAGE_CONTRACT_VERSION},
            )
        if source_manifest.command != "data backfill-daily" or source_manifest.status != "success":
            return self._failed_local_result(
                run_id=run_id,
                command="data audit-daily-coverage",
                started=started,
                validation_result=ValidationResult(
                    dataset_name="daily_coverage",
                    severity=ValidationSeverity.ERROR,
                    check_name="daily_source_run_complete",
                    message=(
                        "Coverage requires a successful data backfill-daily source run; "
                        f"received command={source_manifest.command}, "
                        f"status={source_manifest.status}."
                    ),
                    blocks_output=True,
                ),
                parameters=source_parameters,
                contract_versions={"daily_coverage": DAILY_COVERAGE_CONTRACT_VERSION},
            )

        daily_source = self.storage.read_normalized_dataset_with_provenance(
            "daily",
            run_id=daily_run_id,
        )
        if daily_source is None:
            return self._failed_local_result(
                run_id=run_id,
                command="data audit-daily-coverage",
                started=started,
                validation_result=ValidationResult(
                    dataset_name="daily_coverage",
                    severity=ValidationSeverity.ERROR,
                    check_name="normalized_daily_run_available",
                    message=f"No normalized daily files were found for run {daily_run_id}.",
                    blocks_output=True,
                ),
                parameters=source_parameters,
                contract_versions={"daily_coverage": DAILY_COVERAGE_CONTRACT_VERSION},
            )

        universe_source = self.storage.read_normalized_dataset_with_provenance("universe")
        if universe_source is None:
            return self._failed_local_result(
                run_id=run_id,
                command="data audit-daily-coverage",
                started=started,
                validation_result=ValidationResult(
                    dataset_name="daily_coverage",
                    severity=ValidationSeverity.ERROR,
                    check_name="normalized_universe_available",
                    message="No local normalized universe snapshots were found.",
                    blocks_output=True,
                ),
                parameters=source_parameters,
                contract_versions={"daily_coverage": DAILY_COVERAGE_CONTRACT_VERSION},
            )

        universe_all, _universe_paths = universe_source
        selected_universe, selected_timestamp = _select_universe_snapshot(
            universe_all,
            snapshot_date=snapshot_date,
        )
        selected_universe_paths = _input_paths(selected_universe)
        selected_universe = selected_universe.drop(columns=["__input_path"], errors="ignore")
        prepared_universe = prepare_research_universe(selected_universe, exchange="HOSE")
        validation_results = validate_research_universe(
            prepared_universe,
            expected_input_row_count=len(selected_universe),
        )
        current_symbols = {
            str(symbol)
            for symbol in prepared_universe.loc[
                (prepared_universe["candidate_status"] == "included_candidate")
                & prepared_universe["symbol"].notna(),
                "symbol",
            ].tolist()
        }
        if not current_symbols:
            validation_results.append(
                ValidationResult(
                    dataset_name="daily_coverage",
                    severity=ValidationSeverity.ERROR,
                    check_name="current_snapshot_candidates_available",
                    message="The selected universe snapshot has no included HOSE stock candidates.",
                    blocks_output=True,
                )
            )

        daily_all, daily_paths = daily_source
        coverage = build_daily_coverage_audit(
            daily_all,
            current_universe_symbols=current_symbols,
            requested_symbols=set(source_manifest.symbols),
            universe_snapshot_date=pd.Timestamp(selected_timestamp).date(),
            daily_run_id=daily_run_id,
            start=start,
            end=end,
            config=config,
        )
        validation_results.extend(
            validate_daily_coverage(
                coverage,
                expected_symbol_count=_coverage_symbol_count(
                    daily_all,
                    current_universe_symbols=current_symbols,
                    requested_symbols=set(source_manifest.symbols),
                    start=start,
                    end=end,
                ),
            )
        )
        selected_daily = _select_daily_source_rows(
            daily_all,
            symbols=_clean_optional_symbols(None, daily_all),
            start=start,
            end=end,
        )
        unit_policy = resolve_daily_unit_policy(selected_daily)
        summary = summarize_daily_coverage(coverage)
        output_paths: list[Path] = []
        if not has_blocking_errors(validation_results):
            output_paths.append(
                self.storage.write_parquet(
                    coverage,
                    self.storage.feature_daily_coverage_path(
                        snapshot_date=pd.Timestamp(selected_timestamp).date(),
                        start=start,
                        end=end,
                        run_id=run_id,
                    ),
                )
            )
            report_json, report_markdown = write_daily_coverage_report(
                coverage,
                json_path=(
                    self.settings.report_dir
                    / "data_quality"
                    / f"{run_id}-daily-coverage.json"
                ),
                markdown_path=(
                    self.settings.report_dir
                    / "data_quality"
                    / f"{run_id}-daily-coverage.md"
                ),
                parameters={
                    **source_parameters,
                    "requested_start_date": start.isoformat(),
                    "requested_end_date": end.isoformat(),
                    "selected_snapshot_observed_at_utc": selected_timestamp.isoformat(),
                    "source_ingestion_status": source_manifest.status,
                    "source_ingestion_provider_call_count": source_manifest.provider_call_count,
                },
            )
            output_paths.extend([report_json, report_markdown])

        status = "failed" if has_blocking_errors(validation_results) else "success"
        manifest = build_manifest(
            run_id=run_id,
            command="data audit-daily-coverage",
            started_at_utc=started,
            finished_at_utc=utc_now(),
            status=status,
            exchange="HOSE",
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            row_counts={
                "current_snapshot_candidates": len(current_symbols),
                "selected_normalized_daily": len(selected_daily),
                "audited_symbols": int(summary["audited_symbol_count"]),
                "symbols_with_daily_data": int(summary["symbols_with_daily_data"]),
                "raw_ohlcv_usable_symbols": int(summary["raw_ohlcv_usable_symbol_count"]),
                "vnd_liquidity_usable_symbols": int(
                    summary["vnd_liquidity_usable_symbol_count"]
                ),
                "current_snapshot_vnd_usable_symbols": int(
                    summary["current_snapshot_vnd_usable_symbol_count"]
                ),
            },
            input_paths=sorted(
                set(
                    selected_universe_paths
                    + daily_paths
                    + [self.storage.manifest_path(daily_run_id)]
                )
            ),
            output_paths=output_paths,
            parameters={
                **source_parameters,
                "selected_snapshot_observed_at_utc": selected_timestamp.isoformat(),
                "source_ingestion_manifest": str(self.storage.manifest_path(daily_run_id)),
            },
            unit_provenance=unit_policy,
            data_contract_versions={"daily_coverage": DAILY_COVERAGE_CONTRACT_VERSION},
            notes=[
                "Coverage is computed from one exact successful daily ingestion run.",
                *KNOWN_COVERAGE_RISKS,
            ],
            validation_results=validation_results,
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
        parameters: dict[str, object] | None = None,
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
            parameters=parameters,
            dry_run=True,
        )
        return WorkflowResult(manifest=manifest)

    def _failed_local_result(
        self,
        *,
        run_id: str,
        command: str,
        started: datetime,
        validation_result: ValidationResult,
        parameters: dict[str, object],
        contract_versions: dict[str, str],
    ) -> WorkflowResult:
        manifest = build_manifest(
            run_id=run_id,
            command=command,
            started_at_utc=started,
            finished_at_utc=utc_now(),
            status="failed",
            parameters=parameters,
            data_contract_versions=contract_versions,
            validation_results=[validation_result],
        )
        manifest_path = write_manifest(manifest, self.storage.manifest_root)
        return WorkflowResult(
            manifest=manifest,
            validation_results=[validation_result],
            manifest_path=str(manifest_path),
        )

    def _require_provider(self) -> VnstockDataProvider:
        if self.provider is None:
            self.provider = VnstockDataProvider(self.settings)
        return self.provider

    def _sanitize_provider_error(self, exc: BaseException) -> str:
        secret = (
            self.settings.vnstock_api_key.get_secret_value()
            if self.settings.vnstock_api_key is not None
            else None
        )
        return sanitize_error(exc, [secret] if secret else [])

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

    def _enforce_provider_call_limit(
        self,
        projected_call_count: int,
        *,
        allow_large_universe: bool,
    ) -> None:
        if (
            projected_call_count > self.settings.max_live_provider_calls
            and not allow_large_universe
        ):
            raise SafetyLimitError(
                f"Projected {projected_call_count} provider calls, above safe default "
                f"{self.settings.max_live_provider_calls}. Reduce symbols/date range or re-run "
                "with --allow-large-universe only after confirming quota and runtime impact."
            )


def _clean_symbols(symbols: list[str]) -> list[str]:
    cleaned = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
    if not cleaned:
        raise ValueError("At least one symbol is required.")
    return cleaned


def daily_date_chunks(
    start: date,
    end: date,
    *,
    chunk_calendar_days: int,
) -> list[tuple[date, date]]:
    if start > end:
        raise ValueError("Daily backfill start date must not be after end date.")
    if chunk_calendar_days < 1:
        raise ValueError("chunk_calendar_days must be positive.")
    if chunk_calendar_days > MAX_SAFE_DAILY_CHUNK_CALENDAR_DAYS:
        raise ValueError(
            "chunk_calendar_days cannot exceed the conservative 1,095-day safety boundary."
        )
    chunks: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=chunk_calendar_days - 1), end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def _validate_daily_chunk_bounds(
    frame: pd.DataFrame,
    *,
    symbol: str,
    start: date,
    end: date,
) -> None:
    if frame.empty:
        return
    dates = pd.to_datetime(frame["date"], errors="coerce").dt.date
    outside = dates.notna() & ~dates.between(start, end, inclusive="both")
    if outside.any():
        raise ValueError(
            f"Provider returned {int(outside.sum())} {symbol} rows outside requested chunk "
            f"{start.isoformat()}..{end.isoformat()}."
        )


def _status(validation_results: list[ValidationResult], errors: list[str]) -> str:
    if errors or has_blocking_errors(validation_results):
        return "failed"
    return "success"


def _input_paths(frame: pd.DataFrame) -> list[Path]:
    if "__input_path" not in frame.columns:
        return []
    return sorted({Path(value) for value in frame["__input_path"].dropna().astype(str)})


def _clean_optional_symbols(symbols: list[str] | None, daily: pd.DataFrame) -> list[str]:
    if symbols:
        return _clean_symbols(symbols)
    if "symbol" not in daily.columns:
        return []
    return sorted(daily["symbol"].dropna().astype(str).str.strip().str.upper().unique().tolist())


def _select_daily_source_rows(
    daily: pd.DataFrame,
    *,
    symbols: list[str],
    start: date,
    end: date,
) -> pd.DataFrame:
    if daily.empty:
        return daily.copy()
    dates = pd.to_datetime(daily["date"], errors="coerce").dt.date
    normalized_symbols = daily["symbol"].astype("string").str.strip().str.upper()
    return daily[
        normalized_symbols.isin(symbols) & dates.between(start, end, inclusive="both")
    ].copy()


def _select_universe_snapshot(
    universe: pd.DataFrame,
    *,
    snapshot_date: date | None,
) -> tuple[pd.DataFrame, pd.Timestamp]:
    if "snapshot_timestamp_utc" not in universe.columns:
        raise ValueError("Normalized universe is missing snapshot_timestamp_utc.")
    observed = pd.Series(
        [
            aware_timestamp_to_utc(value, provider="vnstock")
            for value in universe["snapshot_timestamp_utc"]
        ],
        index=universe.index,
        dtype="datetime64[ns, UTC]",
    )
    candidates = (
        universe[observed.dt.date == snapshot_date].copy()
        if snapshot_date is not None
        else universe[observed.notna()].copy()
    )
    if candidates.empty:
        target = snapshot_date.isoformat() if snapshot_date else "any valid date"
        raise ValueError(f"No normalized universe snapshot was found for {target}.")
    candidate_times = pd.Series(
        [
            aware_timestamp_to_utc(value, provider="vnstock")
            for value in candidates["snapshot_timestamp_utc"]
        ],
        index=candidates.index,
        dtype="datetime64[ns, UTC]",
    )
    selected_timestamp = candidate_times.max()
    selected = candidates[candidate_times == selected_timestamp].copy()
    return selected, pd.Timestamp(selected_timestamp)


def _coverage_symbol_count(
    daily: pd.DataFrame,
    *,
    current_universe_symbols: set[str],
    requested_symbols: set[str],
    start: date,
    end: date,
) -> int:
    dates = pd.to_datetime(daily["date"], errors="coerce").dt.normalize()
    in_scope = (
        dates.between(pd.Timestamp(start), pd.Timestamp(end), inclusive="both") | dates.isna()
    )
    symbols = (
        daily.loc[in_scope, "symbol"]
        .dropna()
        .astype("string")
        .str.strip()
        .str.upper()
    )
    observed = {str(symbol) for symbol in symbols if str(symbol)}
    normalized_requested = {
        symbol.strip().upper() for symbol in requested_symbols if symbol.strip()
    }
    return len(current_universe_symbols | normalized_requested | observed)
