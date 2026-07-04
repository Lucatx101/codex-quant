from __future__ import annotations

import importlib.metadata as metadata
import json
import subprocess
from datetime import datetime
from pathlib import Path

from hose_quant.config import PROJECT_ROOT
from hose_quant.data.models import DatasetManifest, ValidationResult
from hose_quant.data.validators import summarize_validation


def create_run_id(command: str, started_at_utc: datetime) -> str:
    safe_command = command.replace(" ", "-").replace("_", "-")
    timestamp = started_at_utc.strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{safe_command}"


def current_git_commit() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package_name in ["hose-quant-system", "vnstock", "pandas", "pyarrow"]:
        try:
            versions[package_name] = metadata.version(package_name)
        except metadata.PackageNotFoundError:
            versions[package_name] = "not-installed"
    return versions


def build_manifest(
    *,
    run_id: str,
    command: str,
    started_at_utc: datetime,
    finished_at_utc: datetime,
    status: str,
    symbols: list[str] | None = None,
    exchange: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    resolution: str | None = None,
    row_counts: dict[str, int] | None = None,
    output_paths: list[Path] | None = None,
    validation_results: list[ValidationResult] | None = None,
    error_summary: list[str] | None = None,
    provider_call_count: int = 0,
    dry_run: bool = False,
) -> DatasetManifest:
    return DatasetManifest(
        run_id=run_id,
        command=command,
        symbols=symbols or [],
        exchange=exchange,
        start_date=start_date,
        end_date=end_date,
        resolution=resolution,
        started_at_utc=started_at_utc,
        finished_at_utc=finished_at_utc,
        status=status,
        row_counts=row_counts or {},
        output_paths=[str(path) for path in output_paths or []],
        validation_summary=summarize_validation(validation_results or []),
        error_summary=error_summary or [],
        package_versions=package_versions(),
        git_commit_hash=current_git_commit(),
        provider_call_count=provider_call_count,
        dry_run=dry_run,
    )


def write_manifest(manifest: DatasetManifest, manifest_root: Path) -> Path:
    manifest_root.mkdir(parents=True, exist_ok=True)
    path = manifest_root / f"{manifest.run_id}.json"
    payload = json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True)
    path.write_text(payload + "\n", encoding="utf-8")
    return path
