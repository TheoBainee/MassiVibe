"""Tests mapping tickers Yahoo."""

import pytest

from myquantstore.instruments import Instrument, InstrumentType
from myquantstore.tickers.yahoo_map import (
    UnmappableTickerError,
    is_skipped_stock_symbol,
    to_yahoo_ticker,
)


def test_identity():
    inst = Instrument(InstrumentType.STOCKS, "AAPL")
    assert to_yahoo_ticker(inst) == "AAPL"


def test_dot_to_dash():
    inst = Instrument(InstrumentType.STOCKS, "BRK.A")
    assert to_yahoo_ticker(inst) == "BRK-A"


def test_override():
    inst = Instrument(InstrumentType.STOCKS, "FOO.WS")
    assert to_yahoo_ticker(inst, {"FOO.WS": "FOO-WT"}) == "FOO-WT"


def test_skip_warrant():
    assert is_skipped_stock_symbol("ACHR.WS")
    with pytest.raises(UnmappableTickerError, match="skippé"):
        to_yahoo_ticker(Instrument(InstrumentType.STOCKS, "ACHR.WS"))


def test_skip_unit():
    with pytest.raises(UnmappableTickerError):
        to_yahoo_ticker(Instrument(InstrumentType.STOCKS, "AAC.U"))


def test_non_stocks():
    with pytest.raises(UnmappableTickerError, match="stocks only"):
        to_yahoo_ticker(Instrument(InstrumentType.FOREX, "EURUSD"))
