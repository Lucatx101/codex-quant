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
        dataset_dir = self.normalized_root / dataset
        if not dataset_dir.exists():
            return None
        paths = sorted(dataset_dir.glob("**/*.parquet"))
        if not paths:
            return None
        return pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True)


def _coerce_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return pd.Timestamp(value).date()
