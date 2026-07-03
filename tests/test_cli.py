from __future__ import annotations

from pydantic import SecretStr

from hose_quant import cli
from hose_quant.config import AppSettings


def test_cli_live_missing_key_fails(monkeypatch, tmp_path, capsys) -> None:
    settings = AppSettings(
        _env_file=None, data_dir=tmp_path / "data", report_dir=tmp_path / "reports"
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    exit_code = cli.main(["audit-data"])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "VNSTOCK_API_KEY is required" in captured.err


def test_cli_offline_writes_reports(monkeypatch, tmp_path) -> None:
    settings = AppSettings(
        _env_file=None,
        vnstock_api_key=SecretStr("dummy-offline-value"),
        data_dir=tmp_path / "data",
        report_dir=tmp_path / "reports",
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    exit_code = cli.main(["audit-data", "--offline"])
    assert exit_code == 0
    assert (tmp_path / "reports" / "data_capabilities.json").exists()
    assert (tmp_path / "docs" / "data-capability-report.md").exists()
