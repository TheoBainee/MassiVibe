"""Tests allocate discrète + panier synthétique (rebase)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from myquantstore.analytics.allocate import allocate_discrete, latest_prices_from_panel
from myquantstore.analytics.optimize import PortfolioResult
from myquantstore.analytics.panel import PricePanel
from myquantstore.analytics.synthetic import build_portfolio_ohlcv
from myquantstore.instruments import Instrument, InstrumentType
from myquantstore.pipeline.aggregator import aggregate
from myquantstore.storage.raw_dumps import save_raw_dump


def test_allocate_sums_and_cash():
    result = PortfolioResult(
        weights={"AAA": 0.5, "BBB": 0.5},
        mean_ann=0.1,
        vol_ann=0.2,
        sharpe=0.5,
        objective="equal",
        symbols=["AAA", "BBB"],
    )
    prices = {"AAA": 100.0, "BBB": 50.0}
    alloc = allocate_discrete(result, prices, value=1000.0)
    assert alloc.invested + alloc.cash == pytest.approx(1000.0)
    assert alloc.invested <= 1000.0 + 1e-9
    assert sum(alloc.shares.values()) >= 1
    # notional matches
    notional = sum(alloc.shares[s] * prices[s] for s in alloc.shares)
    assert notional == pytest.approx(alloc.invested)
    assert abs(sum(alloc.weights_eff.values()) - 1.0) < 1e-9


def test_allocate_skips_unaffordable():
    result = PortfolioResult(
        weights={"CHEAP": 0.5, "RICH": 0.5},
        mean_ann=0.1,
        vol_ann=0.2,
        sharpe=0.5,
        objective="max-sharpe",
        symbols=["CHEAP", "RICH"],
    )
    prices = {"CHEAP": 10.0, "RICH": 50_000.0}
    alloc = allocate_discrete(result, prices, value=1000.0)
    assert "RICH" not in alloc.shares
    assert alloc.shares.get("CHEAP", 0) >= 1


def test_allocate_cheap_tiny_weight_not_overbought():
    """Régression CPRT : w_th≈0.2% ne doit pas absorber le cash résiduel."""
    result = PortfolioResult(
        weights={"COST": 0.65, "AAPL": 0.30, "CPRT": 0.002, "OTHER": 0.048},
        mean_ann=0.1,
        vol_ann=0.2,
        sharpe=0.5,
        objective="min-vol",
        symbols=["COST", "AAPL", "CPRT", "OTHER"],
    )
    prices = {"COST": 900.0, "AAPL": 200.0, "CPRT": 30.0, "OTHER": 100.0}
    value = 20_000.0
    alloc = allocate_discrete(result, prices, value=value)
    # target CPRT ≈ 0.002*20000/30 ≈ 1.33 → max ceil = 2
    assert alloc.shares.get("CPRT", 0) <= 2
    w_eff_cprt = alloc.weights_eff.get("CPRT", 0.0)
    assert w_eff_cprt < 0.01  # pas 8%+
    # pas d'écart monstrueux sur le cheap
    assert abs(w_eff_cprt - alloc.weights_th["CPRT"]) < 0.01


def test_latest_prices_from_panel():
    prices = pl.DataFrame(
        {
            "date": [date(2024, 1, 1), date(2024, 1, 2)],
            "AAA": [10.0, 11.0],
            "BBB": [20.0, 22.0],
        }
    )
    panel = PricePanel(prices=prices, symbols=["AAA", "BBB"])
    latest = latest_prices_from_panel(panel)
    assert latest["AAA"] == 11.0
    assert latest["BBB"] == 22.0


def _seed_stock_1day(symbol: str, settings, *, n: int = 40, base: float = 100.0) -> Instrument:
    inst = Instrument(type=InstrumentType.STOCKS, symbol=symbol)
    today = datetime(2025, 6, 1)
    ts = [today + timedelta(days=i) for i in range(n)]
    closes = [base * (1.01**i) for i in range(n)]
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
            "run_id": ["t"] * n,
            "session_end_date": [t.date() for t in ts],
            "dollar_volume": [c * 1000 for c in closes],
            "transactions": [10] * n,
        }
    )
    save_raw_dump(df, inst, symbol, "20250601T120000", settings, resolution="1day", source="yahoo")
    aggregate(inst, settings, resolution="1day")
    return inst


def test_build_portfolio_ohlcv_rebase(tmp_settings):
    _seed_stock_1day("PAAA", tmp_settings, base=100.0)
    _seed_stock_1day("PBBB", tmp_settings, base=50.0)
    df = build_portfolio_ohlcv(
        {"PAAA": 0.6, "PBBB": 0.4},
        tmp_settings,
        resolution="1day",
        rebase=100.0,
    )
    assert df.height >= 2
    assert abs(float(df["close"][0]) - 100.0) < 1e-6
    assert "open" in df.columns and "volume" in df.columns
    # close croît (les deux legs croissent)
    assert float(df["close"][-1]) > float(df["close"][0])


def test_build_portfolio_ohlcv_empty_weights(tmp_settings):
    with pytest.raises(ValueError, match="poids"):
        build_portfolio_ohlcv({}, tmp_settings)
