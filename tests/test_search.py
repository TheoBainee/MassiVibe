"""Tests de massivibe.tickers.search."""

from __future__ import annotations

import polars as pl

from massivibe.instruments import InstrumentType
from massivibe.tickers.search import (
    market_to_instrument_type,
    rows_for_config_add,
    search_tickers,
    strip_api_prefix,
)


def _df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "ticker": ["AAPL", "MSFT", "EURUSD", "I:NDX", "BTCUSD"],
            "name": [
                "Apple Inc",
                "Microsoft Corp",
                "Euro / US Dollar",
                "Nasdaq 100",
                "Bitcoin",
            ],
            "market": ["stocks", "stocks", "fx", "indices", "crypto"],
            "type": ["CS", "CS", "C", "index", "crypto"],
            "active": [True, True, True, True, True],
            "primary_exchange": ["XNAS", "XNAS", None, None, None],
        }
    )


def test_strip_api_prefix():
    assert strip_api_prefix("C:EURUSD") == "EURUSD"
    assert strip_api_prefix("I:NDX") == "NDX"
    assert strip_api_prefix("AAPL") == "AAPL"
    assert strip_api_prefix("  O:XYZ  ") == "XYZ"


def test_market_to_instrument_type():
    assert market_to_instrument_type("stocks") == InstrumentType.STOCKS
    assert market_to_instrument_type("otc") == InstrumentType.STOCKS
    assert market_to_instrument_type("fx") == InstrumentType.FOREX
    assert market_to_instrument_type("indices") == InstrumentType.INDICES
    assert market_to_instrument_type("crypto") is None


def test_search_by_query_name():
    df = search_tickers(_df(), query="apple")
    assert df.height == 1
    assert df["ticker"][0] == "AAPL"


def test_search_by_ticker_exact():
    df = search_tickers(_df(), ticker="msft")
    assert df.height == 1
    assert df["ticker"][0] == "MSFT"


def test_search_by_market_and_type():
    df = search_tickers(_df(), market="stocks", ticker_type="CS")
    assert df.height == 2


def test_search_by_markets_list():
    df = search_tickers(_df(), markets=["stocks", "fx"])
    assert set(df["market"].to_list()) == {"stocks", "fx"}


def test_search_limit():
    df = search_tickers(_df(), market="stocks", limit=1)
    assert df.height == 1


def test_rows_for_config_add_skips_crypto():
    items = rows_for_config_add(_df())
    symbols = [s for _, s in items]
    assert "AAPL" in symbols
    assert "EURUSD" in symbols
    assert "NDX" in symbols  # strip I:
    assert "BTCUSD" not in symbols
    types = {t for t, _ in items}
    assert InstrumentType.STOCKS in types
    assert InstrumentType.FOREX in types
    assert InstrumentType.INDICES in types
