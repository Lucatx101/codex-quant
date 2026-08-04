from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd

from hose_quant.config import AppSettings
from hose_quant.data.contracts import (
    AVAILABILITY_CONTRACT_VERSION,
    DAILY_PANEL_CONTRACT_VERSION,
    LIQUIDITY_CONTRACT_VERSION,
    UNIVERSE_CONTRACT_VERSION,
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
    validate_daily_ohlcv,
    validate_daily_panel,
    validate_intraday_bars,
    validate_liquidity_characterization,
    validate_quote_snapshot,
    validate_research_universe,
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
        source_unit_provenance = provider.daily_unit_provenance()
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
                normalized_frames.append(
                    normalize_daily_ohlcv(
                        raw,
                        symbol=symbol,
                        exchange="HOSE",
                        unit_provenance=source_unit_provenance,
                    )
                )
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
            },
            output_paths=output_paths,
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

    def prepare_universe(
        self,
        *,
        exchange: str,
        snapshot_date: date | None,
        requested_reference_date: date | None,
        with_liquidity: bool,
        liquidity_reference_date: date | None,
        liquidity_config: LiquidityScreenConfig,
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
            daily_source = self.storage.read_normalized_dataset_with_provenance("daily")
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
    ) -> WorkflowResult:
        started = utc_now()
        run_id = create_run_id("build-daily-panel", started)
        daily_source = self.storage.read_normalized_dataset_with_provenance("daily")
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
