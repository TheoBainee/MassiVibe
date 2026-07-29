"""Tests resample extraday."""

from datetime import date, datetime

import polars as pl

from myquantstore.query.resampler import resample_extraday


def _daily(n: int = 10) -> pl.DataFrame:
    rows = []
    for i in range(n):
        d = date(2024, 1, 1 + i)
        rows.append(
            {
                "window_start": datetime(2024, 1, 1 + i),
                "session_end_date": d,
                "ticker": "AAPL",
                "open": 100.0 + i,
                "high": 101.0 + i,
                "low": 99.0 + i,
                "close": 100.5 + i,
                "volume": 1000 + i,
            }
        )
    return pl.DataFrame(rows).with_columns(pl.col("window_start").cast(pl.Datetime("ns")))


def test_k_days_1_noop():
    df = _daily(5)
    out = resample_extraday(df, 1)
    assert out.height == 5
    assert "candle_count" in out.columns


def test_k_days_2():
    df = _daily(10)
    out = resample_extraday(df, 2)
    # 10 days → 5 full buckets of 2
    assert out.height == 5
    assert out["candle_count"].to_list() == [2] * 5
    assert "bucket_start" in out.columns


def test_partial_dropped():
    df = _daily(5)
    out = resample_extraday(df, 2)
    # 2 full + 1 partial dropped → 2
    assert out.height == 2
