"""Integration CLI : portfolio optimize -i AAPL -i MSFT sur tmp data_dir."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from myquantstore.cli import main
from myquantstore.config import Settings
from myquantstore.instruments import Instrument, InstrumentType
from myquantstore.pipeline.aggregator import aggregate
from myquantstore.storage.raw_dumps import save_raw_dump


def _seed_yahoo_actions_empty(symbol: str, settings) -> None:
    """Cache splits/dividends Yahoo vide + frais → pas d'appel réseau en query."""
    from datetime import UTC, datetime

    import polars as pl

    from myquantstore.storage.parquet_io import write_parquet

    now = datetime.now(UTC).isoformat()
    for kind in ("splits", "dividends"):
        path = settings.yahoo_actions_path(symbol, kind)
        if kind == "splits":
            df = pl.DataFrame(
                schema={
                    "execution_date": pl.Date,
                    "historical_adjustment_factor": pl.Float64,
                    "split_ratio": pl.Float64,
                }
            )
        else:
            df = pl.DataFrame(
                schema={
                    "ex_dividend_date": pl.Date,
                    "historical_adjustment_factor": pl.Float64,
                    "amount": pl.Float64,
                }
            )
        write_parquet(
            df,
            path,
            kind=f"yahoo_actions.{kind}",
            symbol=symbol,
            last_fetched_at=now,
        )



def _seed(symbol: str, settings: Settings, *, base: float, n: int = 120) -> None:
    inst = Instrument(type=InstrumentType.STOCKS, symbol=symbol)
    t0 = datetime(2023, 1, 3)
    ts = [t0 + timedelta(days=i) for i in range(n)]
    closes = [base * (1.001**i) for i in range(n)]
    df = pl.DataFrame(
        {
            "window_start": ts,
            "ticker": [symbol] * n,
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [1000] * n,
            "symbol": [symbol] * n,
            "instrument_type": ["stocks"] * n,
            "product_code": [symbol] * n,
            "run_id": ["cli"] * n,
            "session_end_date": [t.date() for t in ts],
            "dollar_volume": [c * 1000 for c in closes],
            "transactions": [10] * n,
        }
    )
    save_raw_dump(
        df, inst, symbol, "20230103T120000", settings, resolution="1day", source="yahoo"
    )
    aggregate(inst, settings, resolution="1day")
    _seed_yahoo_actions_empty(symbol, settings)


def test_cli_portfolio_optimize_aapl_msft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    data = tmp_path / "data"
    cache = tmp_path / "cache"
    logs = tmp_path / "logs"
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    data.mkdir()
    cache.mkdir()
    logs.mkdir()

    # Config utilisateur isolée avec stocks AAPL/MSFT
    conf = xdg / "config.toml"
    conf.write_text(
        f"""
[instruments]
stocks = ["AAPL", "MSFT"]
futures = []
forex = []
indices = []
options = []

[portfolio]
rf_source = "static"
risk_free_rate = 0.03
min_coverage = 0.5
default_lookback_years = 10

[storage]
data_dir = "{data}"
cache_dir = "{cache}"
log_dir = "{logs}"
""",
        encoding="utf-8",
    )
    (xdg / ".env").write_text("MASSIVE_API_KEY=test_key\n", encoding="utf-8")

    monkeypatch.setattr("myquantstore.config.get_user_config_dir", lambda: xdg)
    monkeypatch.setattr("myquantstore.cli.get_user_config_path", lambda: conf)
    monkeypatch.setattr("myquantstore.cli.get_user_env_path", lambda: xdg / ".env")

    settings = Settings(
        api_key="test_key",
        stocks=["AAPL", "MSFT"],
        futures=[],
        data_dir=str(data),
        cache_dir=str(cache),
        log_dir=str(logs),
        portfolio_rf_source="static",
        portfolio_risk_free_rate=0.03,
        portfolio_min_coverage=0.5,
        portfolio_default_lookback_years=10,
        requests_per_minute=0,
    )
    _seed("AAPL", settings, base=150.0)
    _seed("MSFT", settings, base=300.0)

    rc = main(
        [
            "portfolio",
            "optimize",
            "--objective",
            "max-sharpe",
            "-i",
            "AAPL",
            "-i",
            "MSFT",
            "--rf",
            "0.03",
            "--from",
            "2023-01-01",
            "--to",
            "2023-12-31",
            "--no-div",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "AAPL" in out or "MSFT" in out
    assert "max-sharpe" in out.lower() or "sharpe" in out.lower()
    assert "rf=" in out.lower() or "0.03" in out or "3.00%" in out
