"""Tests de massivibe.tickers.search."""

from __future__ import annotations

import polars as pl

from massivibe.instruments import InstrumentType
from massivibe.tickers.search import (
    distinct_column_values,
    join_ticker_types,
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
            "currency_name": ["usd", "usd", None, None, "usd"],
        }
    )


def _types_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "code": ["CS", "C", "index", "crypto"],
            "description": [
                "Common Stock",
                "Currency Pair",
                "Index",
                "Crypto Currency",
            ],
            "asset_class": ["stocks", "fx", "indices", "crypto"],
            "locale": ["us", "global", "us", "global"],
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


def test_join_ticker_types_adds_description():
    joined = join_ticker_types(_df(), _types_df())
    assert "type_description" in joined.columns
    aapl = joined.filter(pl.col("ticker") == "AAPL")
    assert aapl["type_description"][0] == "Common Stock"
    eurusd = joined.filter(pl.col("ticker") == "EURUSD")
    assert eurusd["type_description"][0] == "Currency Pair"


def test_join_ticker_types_empty_types_noop():
    empty_types = pl.DataFrame(
        schema={
            "code": pl.Utf8,
            "description": pl.Utf8,
            "asset_class": pl.Utf8,
            "locale": pl.Utf8,
        }
    )
    out = join_ticker_types(_df(), empty_types)
    assert "type_description" not in out.columns
    assert out.height == _df().height


def test_distinct_column_values():
    result = distinct_column_values(_df())
    assert "market" in result
    markets = dict(zip(result["market"]["value"].to_list(), result["market"]["count"].to_list()))
    assert markets["stocks"] == 2
    assert "type" in result
    assert "primary_exchange" in result
    # null exchanges excluded → only XNAS
    assert result["primary_exchange"]["value"].to_list() == ["XNAS"]
    assert result["currency_name"]["value"].to_list() == ["usd"]
