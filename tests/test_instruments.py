"""Tests du module instruments.py."""

from __future__ import annotations

import pytest

from massivibe.instruments import Instrument, InstrumentType, parse_timeframe


class TestInstrumentType:
    def test_has_contracts(self):
        assert InstrumentType.FUTURES.has_contracts is True
        assert InstrumentType.OPTIONS.has_contracts is True
        assert InstrumentType.FOREX.has_contracts is False
        assert InstrumentType.STOCKS.has_contracts is False
        assert InstrumentType.INDICES.has_contracts is False

    def test_implemented(self):
        assert InstrumentType.FUTURES.implemented is True
        assert InstrumentType.STOCKS.implemented is True
        assert InstrumentType.FOREX.implemented is True
        assert InstrumentType.INDICES.implemented is True
        assert InstrumentType.OPTIONS.implemented is False


class TestInstrument:
    def test_key(self):
        es = Instrument(type=InstrumentType.FUTURES, symbol="ES")
        assert es.key == "futures:ES"

    def test_api_ticker_prefixes(self):
        assert Instrument(InstrumentType.FUTURES, "ES").api_ticker == "ES"
        assert Instrument(InstrumentType.STOCKS, "AAPL").api_ticker == "AAPL"
        assert Instrument(InstrumentType.FOREX, "EURUSD").api_ticker == "C:EURUSD"
        assert Instrument(InstrumentType.INDICES, "NDX").api_ticker == "I:NDX"
        assert Instrument(InstrumentType.OPTIONS, "AAPL240315C00150000").api_ticker == "O:AAPL240315C00150000"

    def test_path_segment(self):
        assert Instrument(InstrumentType.FUTURES, "ES").path_segment == "futures"
        assert Instrument(InstrumentType.STOCKS, "AAPL").path_segment == "stocks"

    def test_frozen(self):
        """Instrument est immutable."""
        from dataclasses import FrozenInstanceError

        es = Instrument(type=InstrumentType.FUTURES, symbol="ES")
        with pytest.raises(FrozenInstanceError):
            es.symbol = "NQ"  # type: ignore[misc]

    def test_str_is_key(self):
        es = Instrument(type=InstrumentType.FUTURES, symbol="ES")
        assert str(es) == "futures:ES"


class TestParseTimeframe:
    @pytest.mark.parametrize(
        "tf,expected",
        [
            ("1min", (1, "minute")),
            ("5min", (5, "minute")),
            ("15min", (15, "minute")),
            ("1hour", (1, "hour")),
            ("2hour", (2, "hour")),
        ],
    )
    def test_valid(self, tf, expected):
        assert parse_timeframe(tf) == expected

    def test_invalid(self):
        with pytest.raises(ValueError, match="non reconnu"):
            parse_timeframe("bogus")

    def test_case_insensitive(self):
        assert parse_timeframe("1MIN") == (1, "minute")
