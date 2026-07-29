"""Tests du module chains.py (InstrumentChain, SingleSymbolChain, OptionsChain, build_chain)."""

from __future__ import annotations

from datetime import date

import pytest

from myquantstore.chains import OptionsChain, SingleSymbolChain, build_chain
from myquantstore.instruments import Instrument, InstrumentType


class TestSingleSymbolChain:
    def test_active_contract_returns_symbol(self):
        inst = Instrument(InstrumentType.STOCKS, "AAPL")
        chain = SingleSymbolChain(inst)
        assert chain.active_contract(date(2025, 1, 1)) == "AAPL"
        assert chain.active_contract(date(2099, 12, 31)) == "AAPL"

    def test_tick_size_is_zero(self):
        inst = Instrument(InstrumentType.FOREX, "EURUSD")
        chain = SingleSymbolChain(inst)
        assert chain.tick_size_for_ticker("EURUSD") == 0.0

    def test_continuous_segments_one(self):
        inst = Instrument(InstrumentType.INDICES, "NDX")
        chain = SingleSymbolChain(inst)
        segs = chain.continuous_segments(date(2020, 1, 1), date(2025, 1, 1))
        assert len(segs) == 1
        assert segs[0]["ticker"] == "NDX"

    def test_to_table(self):
        inst = Instrument(InstrumentType.STOCKS, "AAPL")
        chain = SingleSymbolChain(inst)
        table = chain.to_table()
        assert table.height == 1
        assert table["ticker"][0] == "AAPL"

    def test_len(self):
        chain = SingleSymbolChain(Instrument(InstrumentType.STOCKS, "AAPL"))
        assert len(chain) == 1


class TestOptionsChain:
    def test_all_methods_not_implemented(self):
        chain = OptionsChain(Instrument(InstrumentType.OPTIONS, "O:AAPL"))
        with pytest.raises(NotImplementedError):
            chain.active_contract(date(2025, 1, 1))
        with pytest.raises(NotImplementedError):
            chain.continuous_segments(date(2020, 1, 1), date(2025, 1, 1))
        with pytest.raises(NotImplementedError):
            chain.tick_size_for_ticker("X")
        with pytest.raises(NotImplementedError):
            chain.to_table()

    def test_wrong_type_raises(self):
        with pytest.raises(ValueError, match="options"):
            OptionsChain(Instrument(InstrumentType.FUTURES, "ES"))


class TestBuildChain:
    def test_futures_builds_rollover_chain(self, sample_contracts_df, tmp_settings):
        inst = Instrument(InstrumentType.FUTURES, "ES")
        chain = build_chain(inst, contracts_df=sample_contracts_df, days_before_expiry=7)
        # RolloverChain a active_contract
        assert chain.active_contract(date(2025, 6, 1)) is not None
        assert len(chain) == 3

    def test_futures_without_contracts_raises(self):
        inst = Instrument(InstrumentType.FUTURES, "ES")
        with pytest.raises(ValueError, match="contracts_df"):
            build_chain(inst)

    def test_stocks_builds_single_chain(self):
        inst = Instrument(InstrumentType.STOCKS, "AAPL")
        chain = build_chain(inst)
        assert isinstance(chain, SingleSymbolChain)
        assert chain.active_contract(date(2025, 1, 1)) == "AAPL"

    def test_options_builds_options_chain(self):
        inst = Instrument(InstrumentType.OPTIONS, "AAPL")
        chain = build_chain(inst)
        assert isinstance(chain, OptionsChain)

    def test_forex_builds_single_chain(self):
        inst = Instrument(InstrumentType.FOREX, "EURUSD")
        chain = build_chain(inst)
        assert isinstance(chain, SingleSymbolChain)


class TestInstrumentChainProtocol:
    def test_rollover_chain_satisfies_protocol(self, sample_chain):
        """RolloverChain satisfait le protocole InstrumentChain."""
        from myquantstore.chains import InstrumentChain

        assert isinstance(sample_chain, InstrumentChain)

    def test_single_chain_satisfies_protocol(self):
        from myquantstore.chains import InstrumentChain

        chain = SingleSymbolChain(Instrument(InstrumentType.STOCKS, "AAPL"))
        assert isinstance(chain, InstrumentChain)
