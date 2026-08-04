from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class MissingCredentialError(RuntimeError):
    """Raised when a live provider command needs credentials that are absent."""


class AppSettings(BaseSettings):
    """Typed application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
        populate_by_name=True,
    )

    vnstock_api_key: SecretStr | None = Field(default=None, validation_alias="VNSTOCK_API_KEY")
    app_env: str = Field(default="development", validation_alias="APP_ENV")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    data_dir: Path = Field(default=PROJECT_ROOT / "data", validation_alias="DATA_DIR")
    report_dir: Path = Field(default=PROJECT_ROOT / "reports", validation_alias="REPORT_DIR")
    request_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        validation_alias="REQUEST_TIMEOUT_SECONDS",
    )
    max_retry_attempts: int = Field(default=2, ge=1, le=5, validation_alias="MAX_RETRY_ATTEMPTS")
    provider_sleep_seconds: float = Field(
        default=0.0, ge=0, validation_alias="PROVIDER_SLEEP_SECONDS"
    )
    max_quote_symbols: int = Field(default=20, ge=1, validation_alias="MAX_QUOTE_SYMBOLS")
    liquidity_window_weekdays: int = Field(
        default=20, ge=1, validation_alias="LIQUIDITY_WINDOW_WEEKDAYS"
    )
    liquidity_min_history_observations: int = Field(
        default=15, ge=1, validation_alias="LIQUIDITY_MIN_HISTORY_OBSERVATIONS"
    )
    liquidity_min_trading_frequency: float = Field(
        default=0.8, ge=0, le=1, validation_alias="LIQUIDITY_MIN_TRADING_FREQUENCY"
    )
    liquidity_max_zero_volume_frequency: float = Field(
        default=0.2, ge=0, le=1, validation_alias="LIQUIDITY_MAX_ZERO_VOLUME_FREQUENCY"
    )
    liquidity_min_average_volume: float | None = Field(
        default=None, ge=0, validation_alias="LIQUIDITY_MIN_AVERAGE_VOLUME"
    )
    liquidity_min_average_traded_value_vnd: float | None = Field(
        default=None,
        ge=0,
        validation_alias="LIQUIDITY_MIN_AVERAGE_TRADED_VALUE_VND",
    )
    provider: str = Field(default="vnstock", validation_alias="PROVIDER")

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if normalized not in allowed:
            msg = f"LOG_LEVEL must be one of {sorted(allowed)}."
            raise ValueError(msg)
        return normalized

    @field_validator("data_dir", "report_dir")
    @classmethod
    def resolve_path(cls, value: Path) -> Path:
        if value.is_absolute():
            return value
        return (PROJECT_ROOT / value).resolve()

    def require_vnstock_api_key(self) -> str:
        if self.vnstock_api_key is None or not self.vnstock_api_key.get_secret_value():
            msg = (
                "VNSTOCK_API_KEY is required for live vnstock audit commands. "
                "Set it in the environment or run the offline report generation path."
            )
            raise MissingCredentialError(msg)
        return self.vnstock_api_key.get_secret_value()

    def sanitized_dict(self) -> dict[str, str | float | int]:
        return {
            "vnstock_api_key": "set" if self.vnstock_api_key else "missing",
            "app_env": self.app_env,
            "log_level": self.log_level,
            "data_dir": str(self.data_dir),
            "report_dir": str(self.report_dir),
            "request_timeout_seconds": self.request_timeout_seconds,
            "max_retry_attempts": self.max_retry_attempts,
            "provider_sleep_seconds": self.provider_sleep_seconds,
            "max_quote_symbols": self.max_quote_symbols,
            "liquidity_window_weekdays": self.liquidity_window_weekdays,
            "liquidity_min_history_observations": self.liquidity_min_history_observations,
            "liquidity_min_trading_frequency": self.liquidity_min_trading_frequency,
            "liquidity_max_zero_volume_frequency": self.liquidity_max_zero_volume_frequency,
            "liquidity_min_average_volume": (
                self.liquidity_min_average_volume
                if self.liquidity_min_average_volume is not None
                else "unset"
            ),
            "liquidity_min_average_traded_value_vnd": (
                self.liquidity_min_average_traded_value_vnd
                if self.liquidity_min_average_traded_value_vnd is not None
                else "unset"
            ),
            "provider": self.provider,
        }


def load_settings() -> AppSettings:
    return AppSettings()
