"""Tests du package pipeline/fetchers (dispatch multi-type + scaffold options)."""

from __future__ import annotations

import pytest

from massivibe.instruments import Instrument, InstrumentType
from massivibe.pipeline.fetchers import get_fetcher
from massivibe.pipeline.fetchers.base import InstrumentFetcher
from massivibe.pipeline.fetchers.futures import FuturesFetcher
from massivibe.pipeline.fetchers.options import OptionsFetcher
from massivibe.pipeline.fetchers.stocks import StocksFetcher


class TestGetFetcher:
    def test_futures_returns_futures_fetcher(self):
        fetcher = get_fetcher(Instrument(InstrumentType.FUTURES, "ES"))
        assert isinstance(fetcher, FuturesFetcher)
        assert isinstance(fetcher, InstrumentFetcher)

    def test_stocks_returns_stocks_fetcher(self):
        fetcher = get_fetcher(Instrument(InstrumentType.STOCKS, "AAPL"))
        assert isinstance(fetcher, StocksFetcher)

    def test_options_returns_options_fetcher(self):
        fetcher = get_fetcher(Instrument(InstrumentType.OPTIONS, "AAPL"))
        assert isinstance(fetcher, OptionsFetcher)

    def test_forex_not_implemented(self):
        """forex n'est pas implémenté (Phase 4)."""
        with pytest.raises(NotImplementedError, match="Phase 4"):
            get_fetcher(Instrument(InstrumentType.FOREX, "EURUSD"))

    def test_indices_not_implemented(self):
        with pytest.raises(NotImplementedError, match="Phase 4"):
            get_fetcher(Instrument(InstrumentType.INDICES, "NDX"))


class TestOptionsFetcherScaffold:
    def test_fetch_raises_not_implemented(self, tmp_settings):
        from massivibe.api.client import MassiveClient

        fetcher = OptionsFetcher()
        client = MassiveClient(tmp_settings)
        try:
            with pytest.raises(NotImplementedError, match="options"):
                fetcher.fetch(
                    Instrument(InstrumentType.OPTIONS, "AAPL"),
                    tmp_settings,
                    client,
                )
        finally:
            client.close()
