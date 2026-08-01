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


def test_override_typed_key():
    inst = Instrument(InstrumentType.FOREX, "EURUSD")
    assert to_yahoo_ticker(inst, {"forex:EURUSD": "EURUSD=X"}) == "EURUSD=X"


def test_skip_warrant():
    assert is_skipped_stock_symbol("ACHR.WS")
    with pytest.raises(UnmappableTickerError, match="skippé"):
        to_yahoo_ticker(Instrument(InstrumentType.STOCKS, "ACHR.WS"))


def test_skip_unit():
    with pytest.raises(UnmappableTickerError):
        to_yahoo_ticker(Instrument(InstrumentType.STOCKS, "AAC.U"))


def test_forex_equals_x():
    assert to_yahoo_ticker(Instrument(InstrumentType.FOREX, "EURUSD")) == "EURUSD=X"
    assert to_yahoo_ticker(Instrument(InstrumentType.FOREX, "gbpusd")) == "GBPUSD=X"
    # déjà suffixé
    assert to_yahoo_ticker(Instrument(InstrumentType.FOREX, "EURUSD=X")) == "EURUSD=X"


def test_indices_caret():
    assert to_yahoo_ticker(Instrument(InstrumentType.INDICES, "NDX")) == "^NDX"
    assert to_yahoo_ticker(Instrument(InstrumentType.INDICES, "^NDX")) == "^NDX"


def test_futures_continuous():
    assert to_yahoo_ticker(Instrument(InstrumentType.FUTURES, "ES")) == "ES=F"
    assert to_yahoo_ticker(Instrument(InstrumentType.FUTURES, "NQ")) == "NQ=F"
    assert to_yahoo_ticker(Instrument(InstrumentType.FUTURES, "ES=F")) == "ES=F"


def test_options_unmappable():
    with pytest.raises(UnmappableTickerError, match="non supporté"):
        to_yahoo_ticker(Instrument(InstrumentType.OPTIONS, "O:AAPL250117C00150000"))
