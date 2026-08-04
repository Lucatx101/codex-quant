from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd


class DataStorage:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.raw_root = data_dir / "raw" / "vnstock"
        self.normalized_root = data_dir / "normalized" / "vnstock"
        self.feature_input_root = data_dir / "feature_inputs" / "vnstock"
        self.cache_root = data_dir / "cache"
        self.manifest_root = data_dir / "manifests"

    def ensure_layout(self) -> None:
        for path in [
            self.raw_root,
            self.normalized_root,
            self.cache_root,
            self.manifest_root,
            self.normalized_root / "universe",
            self.normalized_root / "daily",
            self.normalized_root / "intraday",
            self.normalized_root / "quotes",
            self.feature_input_root / "universe",
            self.feature_input_root / "daily_panel",
            self.feature_input_root / "liquidity",
            self.feature_input_root / "availability",
        ]:
            path.mkdir(parents=True, exist_ok=True)

    def raw_dataset_dir(self, dataset: str, run_id: str) -> Path:
        return self.raw_root / dataset / run_id

    def write_raw_frame(self, dataset: str, run_id: str, frame: pd.DataFrame) -> Path:
        output_dir = self.raw_dataset_dir(dataset, run_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "raw.jsonl"
        frame.to_json(path, orient="records", lines=True, date_format="iso", force_ascii=False)
        return path

    def normalized_universe_path(self, snapshot_date: date, run_id: str) -> Path:
        return (
            self.normalized_root
            / "universe"
            / f"snapshot_date={snapshot_date.isoformat()}"
            / f"{run_id}.parquet"
        )

    def normalized_daily_path(self, symbol: str, run_id: str) -> Path:
        return self.normalized_root / "daily" / f"symbol={symbol.upper()}" / f"{run_id}.parquet"

    def normalized_intraday_path(
        self,
        *,
        resolution: str,
        symbol: str,
        trading_date: date,
        run_id: str,
    ) -> Path:
        return (
            self.normalized_root
            / "intraday"
            / f"resolution={resolution}"
            / f"symbol={symbol.upper()}"
            / f"trading_date={trading_date.isoformat()}"
            / f"{run_id}.parquet"
        )

    def normalized_quotes_path(self, snapshot_date: date, run_id: str) -> Path:
        return (
            self.normalized_root
            / "quotes"
            / f"snapshot_date={snapshot_date.isoformat()}"
            / f"{run_id}.parquet"
        )

    def feature_universe_path(self, snapshot_date: date, run_id: str) -> Path:
        return (
            self.feature_input_root
            / "universe"
            / f"snapshot_date={snapshot_date.isoformat()}"
            / f"{run_id}.parquet"
        )

    def feature_daily_panel_path(self, start: date, end: date, run_id: str) -> Path:
        return (
            self.feature_input_root
            / "daily_panel"
            / f"start_date={start.isoformat()}"
            / f"end_date={end.isoformat()}"
            / f"{run_id}.parquet"
        )

    def feature_liquidity_path(self, reference_date: date, run_id: str) -> Path:
        return (
            self.feature_input_root
            / "liquidity"
            / f"reference_date={reference_date.isoformat()}"
            / f"{run_id}.parquet"
        )

    def feature_availability_path(self, start: date, end: date, run_id: str) -> Path:
        return (
            self.feature_input_root
            / "availability"
            / f"start_date={start.isoformat()}"
            / f"end_date={end.isoformat()}"
            / f"{run_id}.parquet"
        )

    def write_parquet(self, frame: pd.DataFrame, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(path, index=False)
        return path

    def write_daily_partitions(self, frame: pd.DataFrame, run_id: str) -> list[Path]:
        output_paths: list[Path] = []
        for symbol, group in frame.groupby("symbol", dropna=False):
            path = self.normalized_daily_path(str(symbol), run_id)
            output_paths.append(self.write_parquet(group.reset_index(drop=True), path))
        return output_paths

    def write_intraday_partitions(
        self,
        frame: pd.DataFrame,
        *,
        resolution: str,
        run_id: str,
    ) -> list[Path]:
        output_paths: list[Path] = []
        for (symbol, trading_date), group in frame.groupby(
            ["symbol", "trading_date"], dropna=False
        ):
            path = self.normalized_intraday_path(
                resolution=resolution,
                symbol=str(symbol),
                trading_date=_coerce_date(trading_date),
                run_id=run_id,
            )
            output_paths.append(self.write_parquet(group.reset_index(drop=True), path))
        return output_paths

    def read_normalized_dataset(self, dataset: str) -> pd.DataFrame | None:
        result = self.read_normalized_dataset_with_provenance(dataset)
        if result is None:
            return None
        frame, _paths = result
        return frame.drop(columns=["__input_path"], errors="ignore")

    def normalized_dataset_paths(self, dataset: str) -> list[Path]:
        dataset_dir = self.normalized_root / dataset
        if not dataset_dir.exists():
            return []
        return sorted(dataset_dir.glob("**/*.parquet"))

    def read_normalized_dataset_with_provenance(
        self,
        dataset: str,
    ) -> tuple[pd.DataFrame, list[Path]] | None:
        paths = self.normalized_dataset_paths(dataset)
        if not paths:
            return None
        frames = []
        for path in paths:
            frame = pd.read_parquet(path)
            frame["__input_path"] = str(path)
            frames.append(frame)
        return pd.concat(frames, ignore_index=True), paths


def _coerce_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return pd.Timestamp(value).date()
