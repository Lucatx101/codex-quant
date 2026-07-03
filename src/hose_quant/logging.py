from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

SENSITIVE_KEY_PARTS = ("authorization", "api_key", "apikey", "token", "secret", "password")
REDACTION = "[REDACTED]"


def redact_value(value: Any, secrets: list[str] | None = None) -> Any:
    """Redact secret-looking keys and known secret string values."""

    known = [item for item in secrets or [] if item]
    if isinstance(value, Mapping):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(part in key_text for part in SENSITIVE_KEY_PARTS):
                redacted[key] = REDACTION
            else:
                redacted[key] = redact_value(item, known)
        return redacted
    if isinstance(value, list):
        return [redact_value(item, known) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item, known) for item in value)
    if isinstance(value, str):
        output = value
        for secret in known:
            output = output.replace(secret, REDACTION)
        return output
    return value


class RedactingFormatter(logging.Formatter):
    def __init__(self, *args: Any, secrets: list[str] | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._secrets = secrets or []

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        return str(redact_value(rendered, self._secrets))


def configure_logging(level: str = "INFO", secrets: list[str] | None = None) -> None:
    logger = logging.getLogger()
    logger.setLevel(level)
    formatter = RedactingFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
        secrets=secrets,
    )
    if not logger.handlers:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
        return
    for handler in logger.handlers:
        handler.setFormatter(formatter)
