"""Tests client Yahoo (mocks chart API, pas d'appel réseau)."""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

import polars as pl
import pytest

from myquantstore.api.yahoo import (
    compute_dividend_adjustment_factors,
    compute_split_adjustment_factors,
    fetch_actions,
    fetch_daily_ohlcv,
)
from myquantstore.instruments import Instrument, InstrumentType
from myquantstore.pipeline.aggregator import aggregate
from myquantstore.pipeline.fetchers.yahoo_daily import YahooStocksDailyFetcher
from myquantstore.storage.aggregate_cache import aggregate_exists, read_aggregate


def _sample_chart_payload() -> dict:
    # 2 daily bars + 1 split + 1 dividend
    return {
        "chart": {
            "result": [
                {
                    "timestamp": [1704153600, 1704240000],  # 2024-01-02, 2024-01-03 UTC midnights-ish
                    "indicators": {
                        "quote": [
                            {
                                "open": [10.0, 11.0],
                                "high": [12.0, 13.0],
                                "low": [9.0, 10.0],
                                "close": [11.0, 12.0],
                                "volume": [1000, 2000],
                            }
                        ]
                    },
                    "events": {
                        "splits": {
                            "1598832000": {
                                "date": 1598832000,
                                "numerator": 4.0,
                                "denominator": 1.0,
                                "splitRatio": "4:1",
                            }
                        },
                        "dividends": {
                            "1707436800": {
                                "date": 1707436800,
                                "amount": 0.22,
                            }
                        },
                    },
                }
            ],
            "error": None,
        }
    }


def test_compute_split_factors():
    splits = pl.DataFrame(
        {
            "execution_date": [date(2020, 1, 1), date(2022, 1, 1)],
            "split_ratio": [2.0, 4.0],
        }
    )
    out = compute_split_adjustment_factors(splits)
    factors = dict(
        zip(out["execution_date"].to_list(), out["historical_adjustment_factor"].to_list())
    )
    assert factors[date(2022, 1, 1)] == pytest.approx(0.25)
    assert factors[date(2020, 1, 1)] == pytest.approx(0.125)


def test_compute_dividend_factors():
    divs = pl.DataFrame(
        {
            "ex_dividend_date": [date(2024, 6, 1)],
            "amount": [1.0],
        }
    )
    ohlcv = pl.DataFrame(
        {
            "session_end_date": [date(2024, 5, 31), date(2024, 6, 1)],
            "close": [100.0, 99.0],
        }
    )
    out = compute_dividend_adjustment_factors(divs, ohlcv)
    assert out.height == 1
    assert out["historical_adjustment_factor"][0] == pytest.approx(0.99)


@patch("myquantstore.api.yahoo._chart_request")
def test_fetch_daily_ohlcv_mock(mock_chart, tmp_settings):
    mock_chart.return_value = _sample_chart_payload()
    tmp_settings.yahoo_requests_per_minute = 0
    df = fetch_daily_ohlcv("AAPL", tmp_settings, period="max", internal_symbol="AAPL")
    assert df.height == 2
    assert {"window_start", "open", "high", "low", "close", "volume", "ticker"}.issubset(
        df.columns
    )
    assert df["ticker"][0] == "AAPL"
    mock_chart.assert_called_once()


@patch("myquantstore.api.yahoo._chart_request")
def test_fetch_actions_mock(mock_chart, tmp_settings):
    mock_chart.return_value = _sample_chart_payload()
    tmp_settings.yahoo_requests_per_minute = 0
    s, d = fetch_actions("AAPL", tmp_settings)
    assert s.height == 1
    assert d.height == 1
    assert s["split_ratio"][0] == pytest.approx(4.0)
    assert d["amount"][0] == pytest.approx(0.22)


@patch("myquantstore.pipeline.fetchers.yahoo_daily.fetch_chart_bundle")
def test_yahoo_daily_fetcher_end_to_end(mock_bundle, tmp_settings):
    inst = Instrument(InstrumentType.STOCKS, "AAPL")
    tmp_settings.stocks = ["AAPL"]
    tmp_settings.yahoo_requests_per_minute = 0

    ohlcv = pl.DataFrame(
        {
            "window_start": [datetime(2024, 1, 2), datetime(2024, 1, 3)],
            "session_end_date": [date(2024, 1, 2), date(2024, 1, 3)],
            "ticker": ["AAPL", "AAPL"],
            "open": [100.0, 101.0],
            "high": [102.0, 103.0],
            "low": [99.0, 100.0],
            "close": [101.0, 102.0],
            "volume": [1000, 1100],
        }
    ).with_columns(pl.col("window_start").cast(pl.Datetime("ns")))
    mock_bundle.return_value = (
        ohlcv,
        pl.DataFrame(schema={"execution_date": pl.Date, "split_ratio": pl.Float64}),
        pl.DataFrame(schema={"ex_dividend_date": pl.Date, "amount": pl.Float64}),
    )

    client = SimpleNamespace()
    result = YahooStocksDailyFetcher().fetch(inst, tmp_settings, client, force=True)  # type: ignore[arg-type]
    assert result["status"] == "ok"
    assert result["candles"] == 2
    assert aggregate_exists(inst, tmp_settings, resolution="1day")
    agg = read_aggregate(inst, tmp_settings, resolution="1day")
    assert agg.height == 2
    aggregate(inst, tmp_settings, resolution="1day")


@patch("myquantstore.pipeline.fetchers.yahoo_daily.fetch_chart_bundle")
def test_yahoo_daily_stores_raw_after_unadjust(mock_bundle, tmp_settings):
    """OHLC Yahoo déjà adjusted → dump/aggregate en prix bruts."""
    from myquantstore.query.adjust import apply_split_adjustment
    from myquantstore.yahoo_actions.cache import YahooActionsCache

    inst = Instrument(InstrumentType.STOCKS, "AAPL")
    tmp_settings.stocks = ["AAPL"]
    tmp_settings.yahoo_requests_per_minute = 0
    tmp_settings.instrument_cache_ttl_days = 30

    # Chart Yahoo : pré-split déjà /4 (close 125), post-split 130
    ohlcv = pl.DataFrame(
        {
            "window_start": [datetime(2020, 8, 28), datetime(2020, 8, 31)],
            "session_end_date": [date(2020, 8, 28), date(2020, 8, 31)],
            "ticker": ["AAPL", "AAPL"],
            "open": [125.0, 130.0],
            "high": [126.0, 131.0],
            "low": [124.0, 129.0],
            "close": [125.5, 130.5],
            "volume": [1_000_000, 1_100_000],
        }
    ).with_columns(pl.col("window_start").cast(pl.Datetime("ns")))
    splits_raw = pl.DataFrame(
        {"execution_date": [date(2020, 8, 31)], "split_ratio": [4.0]}
    )
    mock_bundle.return_value = (
        ohlcv,
        splits_raw,
        pl.DataFrame(schema={"ex_dividend_date": pl.Date, "amount": pl.Float64}),
    )

    result = YahooStocksDailyFetcher().fetch(
        inst, tmp_settings, SimpleNamespace(), force=True  # type: ignore[arg-type]
    )
    assert result["status"] == "ok"

    agg = read_aggregate(inst, tmp_settings, resolution="1day").sort("window_start")
    # Brut : 125 / 0.25 = 500 pré-split ; post-split inchangé
    assert agg["close"][0] == pytest.approx(502.0)  # 125.5 / 0.25
    assert agg["close"][1] == pytest.approx(130.5)

    splits = YahooActionsCache("AAPL", "splits", tmp_settings).get()
    adjusted = apply_split_adjustment(agg, splits).sort("window_start")
    # Query default : série lisse split-adjusted
    assert adjusted["close"][0] == pytest.approx(125.5)
    assert adjusted["close"][1] == pytest.approx(130.5)


@patch("myquantstore.pipeline.fetchers.yahoo_daily.fetch_chart_bundle")
def test_yahoo_daily_incremental_does_not_wipe_splits(mock_bundle, tmp_settings):
    """Fetch incrémental ne doit pas écraser yahoo_actions avec events partiels."""
    from myquantstore.yahoo_actions.cache import YahooActionsCache
    from myquantstore.api.yahoo import compute_split_adjustment_factors

    inst = Instrument(InstrumentType.STOCKS, "AAPL")
    tmp_settings.stocks = ["AAPL"]
    tmp_settings.yahoo_requests_per_minute = 0
    tmp_settings.instrument_cache_ttl_days = 30
    tmp_settings.yahoo_overlap_buffer_days = 5

    # Seed : full history déjà en aggregate + cache splits complets
    full = pl.DataFrame(
        {
            "window_start": [datetime(2020, 8, 28), datetime(2024, 1, 2)],
            "session_end_date": [date(2020, 8, 28), date(2024, 1, 2)],
            "ticker": ["AAPL", "AAPL"],
            "open": [500.0, 180.0],
            "high": [501.0, 181.0],
            "low": [499.0, 179.0],
            "close": [500.0, 180.0],
            "volume": [1000, 2000],
            "symbol": ["AAPL", "AAPL"],
            "instrument_type": ["stocks", "stocks"],
            "product_code": ["AAPL", "AAPL"],
            "run_id": ["seed", "seed"],
        }
    ).with_columns(pl.col("window_start").cast(pl.Datetime("ns")))
    from myquantstore.storage.raw_dumps import save_raw_dump
    from myquantstore.config import generate_run_ts

    run_ts = generate_run_ts()
    save_raw_dump(
        full,
        inst,
        "AAPL",
        run_ts,
        tmp_settings,
        source_url="test",
        page_count=1,
        resolution="1day",
        source="yahoo",
    )
    aggregate(inst, tmp_settings, resolution="1day")

    full_splits = compute_split_adjustment_factors(
        pl.DataFrame(
            {
                "execution_date": [date(2014, 6, 9), date(2020, 8, 31)],
                "split_ratio": [7.0, 4.0],
            }
        )
    )
    YahooActionsCache("AAPL", "splits", tmp_settings)._write(full_splits, "AAPL")
    YahooActionsCache("AAPL", "dividends", tmp_settings)._write(
        pl.DataFrame(
            schema={
                "ex_dividend_date": pl.Date,
                "amount": pl.Float64,
                "historical_adjustment_factor": pl.Float64,
            }
        ),
        "AAPL",
    )

    # Incrémental : events vides (fenêtre récente sans split)
    recent = pl.DataFrame(
        {
            "window_start": [datetime(2024, 1, 3)],
            "session_end_date": [date(2024, 1, 3)],
            "ticker": ["AAPL"],
            "open": [181.0],
            "high": [182.0],
            "low": [180.0],
            "close": [181.5],
            "volume": [2100],
        }
    ).with_columns(pl.col("window_start").cast(pl.Datetime("ns")))
    mock_bundle.return_value = (
        recent,
        pl.DataFrame(schema={"execution_date": pl.Date, "split_ratio": pl.Float64}),
        pl.DataFrame(schema={"ex_dividend_date": pl.Date, "amount": pl.Float64}),
    )

    result = YahooStocksDailyFetcher().fetch(
        inst, tmp_settings, SimpleNamespace(), force=True  # type: ignore[arg-type]
    )
    assert result["status"] == "ok"

    cached = YahooActionsCache("AAPL", "splits", tmp_settings).get()
    assert cached.height == 2
    assert date(2020, 8, 31) in cached["execution_date"].to_list()
