from __future__ import annotations

import pytest
from pydantic import SecretStr

from hose_quant.config import AppSettings, MissingCredentialError
from hose_quant.logging import redact_value


def test_missing_api_key_has_clear_error(tmp_path) -> None:
    settings = AppSettings(
        _env_file=None, data_dir=tmp_path / "data", report_dir=tmp_path / "reports"
    )
    with pytest.raises(MissingCredentialError, match="VNSTOCK_API_KEY is required"):
        settings.require_vnstock_api_key()


def test_secret_repr_and_sanitized_dict_do_not_expose_key(tmp_path) -> None:
    settings = AppSettings(
        _env_file=None,
        vnstock_api_key=SecretStr("dummy-redaction-value"),
        data_dir=tmp_path / "data",
        report_dir=tmp_path / "reports",
    )
    assert "dummy-redaction-value" not in repr(settings)
    assert settings.sanitized_dict()["vnstock_api_key"] == "set"


def test_redaction_handles_secret_headers() -> None:
    redacted = redact_value(
        {"Authorization": "dummy auth value", "nested": {"api_key": "dummy", "safe": "ok"}},
        secrets=["abc"],
    )
    assert redacted["Authorization"] == "[REDACTED]"
    assert redacted["nested"]["api_key"] == "[REDACTED]"
    assert redacted["nested"]["safe"] == "ok"
