from __future__ import annotations

import json
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest

from hose_quant import cli
from hose_quant.config import PROJECT_ROOT, AppSettings
from hose_quant.data.feature_inputs import (
    UnverifiedLiquidityUnitsError,
    build_daily_panel,
    characterize_liquidity,
    daily_availability_diagnostics,
    prepare_research_universe,
    unverified_unit_policy,
    verified_kbs_ohlcv_unit_policy,
)
from hose_quant.data.market_time import market_time_policy, timestamp_provenance
from hose_quant.data.models import (
    HistoricalMembershipStatus,
    LiquidityScreenConfig,
    LiquidityScreenStatus,
    TimestampAwarenessStatus,
    UnitVerificationStatus,
    UniverseCandidateStatus,
)
from hose_quant.data.normalizers import normalize_daily_ohlcv, normalize_intraday_bars
from hose_quant.data.storage import DataStorage
from hose_quant.data.validators import (
    has_blocking_errors,
    validate_availability_diagnostics,
    validate_daily_panel,
    validate_intraday_bars,
    validate_liquidity_characterization,
    validate_research_universe,
)

FIXED_NOW = datetime(2026, 7, 4, 2, 0, tzinfo=UTC)


def _daily_frame(symbol: str, rows: list[dict[str, object]]) -> pd.DataFrame:
    return normalize_daily_ohlcv(
        pd.DataFrame(rows),
        symbol=symbol,
        exchange="HOSE",
        ingestion_timestamp_utc=FIXED_NOW,
    )


def _valid_daily_rows() -> list[dict[str, object]]:
    return [
        {"time": "2026-06-29", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100},
        {"time": "2026-06-30", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 0},
        {"time": "2026-07-01", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100},
        {"time": "2026-07-02", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100},
        {"time": "2026-07-03", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100},
    ]


def _normalized_universe() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "provider": "vnstock",
                "exchange": "HOSE",
                "symbol": "FPT",
                "organ_name": "FPT",
                "english_organ_name": "FPT",
                "security_type": "stock",
                "provider_id": 1,
                "snapshot_timestamp_utc": FIXED_NOW,
                "raw_exchange_field": "HOSE",
                "raw_type_field": "stock",
            },
            {
                "provider": "vnstock",
                "exchange": "HOSE",
                "symbol": "HPG",
                "organ_name": "HPG",
                "english_organ_name": "HPG",
                "security_type": "stock",
                "provider_id": 2,
                "snapshot_timestamp_utc": FIXED_NOW,
                "raw_exchange_field": "HOSE",
                "raw_type_field": "stock",
            },
        ]
    )


def test_universe_preparation_preserves_rows_and_refuses_historical_claims() -> None:
    frame = pd.concat(
        [
            _normalized_universe(),
            pd.DataFrame(
                [
                    {
                        "provider": "vnstock",
                        "exchange": "HOSE",
                        "symbol": " fpt ",
                        "security_type": "stock",
                        "snapshot_timestamp_utc": FIXED_NOW,
                    },
                    {
                        "provider": "vnstock",
                        "exchange": "HOSE",
                        "symbol": " ",
                        "security_type": "stock",
                        "snapshot_timestamp_utc": FIXED_NOW,
                    },
                    {
                        "provider": "vnstock",
                        "exchange": "HOSE",
                        "symbol": "E1VFVN30",
                        "security_type": "fund",
                        "snapshot_timestamp_utc": FIXED_NOW,
                    },
                    {
                        "provider": "vnstock",
                        "exchange": "HOSE",
                        "symbol": "XYZ",
                        "security_type": "mystery",
                        "snapshot_timestamp_utc": FIXED_NOW,
                    },
                ]
            ),
        ],
        ignore_index=True,
    )
    prepared = prepare_research_universe(
        frame,
        exchange="HOSE",
        requested_reference_date=date(2020, 1, 2),
    )

    assert len(prepared) == len(frame)
    assert not prepared["historical_membership_verified"].any()
    assert set(prepared["historical_membership_status"]) == {
        HistoricalMembershipStatus.REQUESTED_REFERENCE_UNVERIFIED.value
    }
    fpt = prepared[prepared["symbol"] == "FPT"]
    assert set(fpt["candidate_status"]) == {UniverseCandidateStatus.UNCERTAIN.value}
    blank = prepared[prepared["symbol"].isna()].iloc[0]
    assert blank["candidate_status"] == UniverseCandidateStatus.EXCLUDED.value
    fund = prepared[prepared["symbol"] == "E1VFVN30"].iloc[0]
    assert fund["candidate_status"] == UniverseCandidateStatus.EXCLUDED.value
    unknown = prepared[prepared["symbol"] == "XYZ"].iloc[0]
    assert unknown["candidate_status"] == UniverseCandidateStatus.UNCERTAIN.value
    results = validate_research_universe(prepared, expected_input_row_count=len(frame))
    assert not has_blocking_errors(results)


def test_universe_snapshot_naive_timestamp_is_preserved_but_not_localized() -> None:
    frame = _normalized_universe().iloc[[0]].copy()
    frame["snapshot_timestamp_utc"] = datetime(2026, 7, 4, 2, 0)
    prepared = prepare_research_universe(frame, exchange="HOSE")

    assert prepared.loc[0, "source_snapshot_timestamp_raw"] == "2026-07-04 02:00:00"
    assert prepared.loc[0, "source_snapshot_timezone_status"] == "naive"
    assert not bool(prepared.loc[0, "source_snapshot_localization_applied"])
    assert pd.isna(prepared.loc[0, "source_snapshot_observed_at_utc"])
    results = validate_research_universe(prepared, expected_input_row_count=1)
    assert any(result.check_name == "snapshot_timezone_awareness" for result in results)
    assert has_blocking_errors(results)


def test_daily_panel_is_long_form_sorted_and_never_forward_fills() -> None:
    raw = _daily_frame(
        "FPT",
        [
            {"time": "2026-07-03", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100},
            {"time": "2026-07-01", "open": 9, "high": 10, "low": 8, "close": 9, "volume": 50},
        ],
    )
    panel = build_daily_panel(
        raw,
        symbols=["FPT"],
        start=date(2026, 7, 1),
        end=date(2026, 7, 3),
        unit_policy=unverified_unit_policy(),
    )

    assert panel["date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-07-01", "2026-07-03"]
    assert len(panel) == 2
    assert set(panel["observation_status"]) == {"observed_provider_bar"}
    assert set(panel["price_adjustment_status"]) == {"unknown"}
    assert set(panel["unit_verification_status"]) == {UnitVerificationStatus.UNVERIFIED.value}
    assert set(panel["traded_value_unit"]) == {"unavailable"}
    results = validate_daily_panel(panel, expected_source_row_count=2)
    assert not has_blocking_errors(results)
    assert any(result.check_name == "price_adjustment_unknown" for result in results)


def test_daily_panel_validator_blocks_duplicate_keys_and_invalid_ohlc() -> None:
    raw = _daily_frame(
        "FPT",
        [
            {"time": "2026-07-01", "open": 10, "high": 8, "low": 9, "close": 10, "volume": 10},
            {"time": "2026-07-01", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 10},
        ],
    )
    panel = build_daily_panel(
        raw,
        symbols=["FPT"],
        start=date(2026, 7, 1),
        end=date(2026, 7, 1),
        unit_policy=unverified_unit_policy(),
    )
    results = validate_daily_panel(panel, expected_source_row_count=2)
    checks = {result.check_name for result in results}
    assert "duplicate_symbol_date" in checks
    assert "high_gte_open_close" in checks
    assert has_blocking_errors(results)


def test_availability_diagnostics_show_missing_zero_volume_and_absence() -> None:
    daily = _daily_frame(
        "FPT",
        [
            {"time": "2026-06-29", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 0},
            {"time": "2026-07-01", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100},
        ],
    )
    diagnostics = daily_availability_diagnostics(
        daily,
        symbols=["FPT", "VCB"],
        start=date(2026, 6, 29),
        end=date(2026, 7, 3),
    )
    fpt = diagnostics[diagnostics["symbol"] == "FPT"].iloc[0]
    assert fpt["expected_weekday_count"] == 5
    assert fpt["observed_expected_weekday_count"] == 2
    assert fpt["missing_expected_weekday_count"] == 3
    assert fpt["zero_volume_count"] == 1
    vcb = diagnostics[diagnostics["symbol"] == "VCB"].iloc[0]
    assert bool(vcb["absence_of_data"])
    assert vcb["observation_count"] == 0
    assert not has_blocking_errors(validate_availability_diagnostics(diagnostics))


def test_liquidity_is_backward_looking_parameterized_and_preserves_unit_uncertainty() -> None:
    daily = pd.concat(
        [
            _daily_frame(
                "FPT",
                _valid_daily_rows()
                + [
                    {
                        "time": "2026-07-06",
                        "open": 10,
                        "high": 11,
                        "low": 9,
                        "close": 10,
                        "volume": 999999,
                    }
                ],
            ),
            _daily_frame("HPG", _valid_daily_rows()[:2]),
        ],
        ignore_index=True,
    )
    config = LiquidityScreenConfig(
        window_weekdays=5,
        min_history_observations=5,
        min_trading_frequency=0.8,
        max_zero_volume_frequency=0.2,
        min_average_volume_provider_units=90,
    )
    result = characterize_liquidity(
        daily,
        symbols=["FPT", "HPG", "VCB"],
        reference_date=date(2026, 7, 3),
        config=config,
        unit_policy=unverified_unit_policy(),
    )
    fpt = result[result["symbol"] == "FPT"].iloc[0]
    assert fpt["observed_date_count"] == 5
    assert fpt["trading_frequency"] == pytest.approx(0.8)
    assert fpt["zero_volume_frequency"] == pytest.approx(0.2)
    assert fpt["average_volume_provider_units"] == pytest.approx(80)
    assert pd.isna(fpt["average_traded_value_vnd"])
    assert fpt["screen_status"] == LiquidityScreenStatus.FAILED.value
    hpg = result[result["symbol"] == "HPG"].iloc[0]
    assert hpg["screen_status"] == LiquidityScreenStatus.INSUFFICIENT_HISTORY.value
    vcb = result[result["symbol"] == "VCB"].iloc[0]
    assert vcb["screen_status"] == LiquidityScreenStatus.ABSENT_DATA.value
    assert not has_blocking_errors(validate_liquidity_characterization(result))


def test_monetary_liquidity_requires_verified_units_and_uses_explicit_scales() -> None:
    daily = _daily_frame("FPT", _valid_daily_rows())
    monetary_config = LiquidityScreenConfig(
        window_weekdays=5,
        min_history_observations=5,
        min_average_traded_value_vnd=700_000,
    )
    with pytest.raises(UnverifiedLiquidityUnitsError):
        characterize_liquidity(
            daily,
            symbols=["FPT"],
            reference_date=date(2026, 7, 3),
            config=monetary_config,
            unit_policy=unverified_unit_policy(),
        )

    result = characterize_liquidity(
        daily,
        symbols=["FPT"],
        reference_date=date(2026, 7, 3),
        config=monetary_config,
        unit_policy=verified_kbs_ohlcv_unit_policy(),
    )
    assert result.loc[0, "average_traded_value_vnd"] == pytest.approx(800_000)
    assert result.loc[0, "screen_status"] == LiquidityScreenStatus.PASSED.value
    assert result.loc[0, "price_unit"] == "thousand_vnd"


def test_market_time_policy_and_timestamp_provenance_do_not_localize_naive_values() -> None:
    policy = market_time_policy()
    assert policy.target_market_timezone == "Asia/Ho_Chi_Minh"
    assert any(session.name == "lunch_break" for session in policy.sessions)
    assert "holidays_not_applied" in policy.holiday_calendar_status

    naive = timestamp_provenance("2026-07-03 09:15:00", provider="vnstock")
    aware = timestamp_provenance("2026-07-03T09:15:00+07:00", provider="vnstock")
    assert naive.awareness_status is TimestampAwarenessStatus.NAIVE
    assert not naive.localization_applied
    assert naive.source_timezone is None
    assert aware.awareness_status is TimestampAwarenessStatus.AWARE
    assert aware.source_timezone is not None

    normalized = normalize_intraday_bars(
        pd.DataFrame(
            [
                {
                    "time": "2026-07-03 09:15:00",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10,
                    "volume": 100,
                }
            ]
        ),
        symbol="FPT",
        resolution="1m",
        ingestion_timestamp_utc=FIXED_NOW,
    )
    assert normalized.loc[0, "provider_timestamp_raw"] == "2026-07-03 09:15:00"
    assert normalized.loc[0, "timestamp_timezone_status"] == "naive"
    assert not bool(normalized.loc[0, "timestamp_localization_applied"])
    assert not has_blocking_errors(validate_intraday_bars(normalized))


def test_local_feature_input_cli_writes_outputs_and_manifest_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = AppSettings(
        _env_file=None,
        data_dir=tmp_path / "data",
        report_dir=tmp_path / "reports",
    )
    storage = DataStorage(settings.data_dir)
    storage.ensure_layout()
    daily = _daily_frame("FPT", _valid_daily_rows())
    storage.write_parquet(daily, storage.normalized_daily_path("FPT", "source-run"))
    storage.write_parquet(
        _normalized_universe(),
        storage.normalized_universe_path(FIXED_NOW.date(), "source-run"),
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    panel_exit = cli.main(
        [
            "data",
            "build-daily-panel",
            "--symbols",
            "FPT,HPG",
            "--start",
            "2026-06-29",
            "--end",
            "2026-07-03",
        ]
    )
    universe_exit = cli.main(
        [
            "data",
            "prepare-universe",
            "--with-liquidity",
            "--liquidity-reference-date",
            "2026-07-03",
            "--window-weekdays",
            "5",
            "--min-history-observations",
            "2",
        ]
    )
    captured = capsys.readouterr()
    assert panel_exit == 0
    assert universe_exit == 0
    assert "Status: success" in captured.out
    assert list((settings.data_dir / "feature_inputs" / "vnstock").glob("**/*.parquet"))
    manifests = list((settings.data_dir / "manifests").glob("*.json"))
    assert len(manifests) == 2
    payloads = [json.loads(path.read_text()) for path in manifests]
    assert all(payload["provider_call_count"] == 0 for payload in payloads)
    assert all(payload["input_paths"] for payload in payloads)


def test_feature_input_generated_paths_are_ignored_by_git() -> None:
    paths = [
        "data/feature_inputs/vnstock/daily_panel/start_date=2026-01-01/test.parquet",
        "reports/feature_inputs/test-availability.json",
    ]
    result = subprocess.run(
        ["git", "check-ignore", *paths],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert set(paths) <= set(result.stdout.splitlines())
