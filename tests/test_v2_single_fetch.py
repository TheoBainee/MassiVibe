"""Tests end-to-end du fetcher V2 mono-symbole (forex + indices, Phase 4).

Valide : fetch v2 → dump brut → agrégation → query (sans split/adjust).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from myquantstore.api.client import MassiveClient
from myquantstore.instruments import Instrument, InstrumentType
from myquantstore.pipeline.fetchers.v2_single import V2SingleSymbolFetcher
from myquantstore.query.reader import query
from myquantstore.storage.aggregate_cache import aggregate_exists, read_aggregate


@pytest.fixture
def forex_settings(tmp_settings):
    """Settings isolés avec forex=['EURUSD']."""
    return tmp_settings.model_copy(
        update={"futures": [], "stocks": [], "forex": ["EURUSD"], "indices": []}
    )


@pytest.fixture
def indices_settings(tmp_settings):
    """Settings isolés avec indices=['NDX']."""
    return tmp_settings.model_copy(
        update={"futures": [], "stocks": [], "forex": [], "indices": ["NDX"]}
    )


@pytest.fixture
def forex_client(forex_settings):
    c = MassiveClient(forex_settings)
    yield c
    c.close()


@pytest.fixture
def indices_client(indices_settings):
    c = MassiveClient(indices_settings)
    yield c
    c.close()


def _forex_aggs_response() -> dict:
    """2 chandeliers 1min EURUSD (avec volume)."""
    return {
        "status": "OK",
        "results": [
            {
                "o": 1.0850,
                "h": 1.0855,
                "l": 1.0848,
                "c": 1.0852,
                "v": 1200,
                "n": 40,
                "t": 1598520600000,
                "vw": 1.0851,
            },
            {
                "o": 1.0852,
                "h": 1.0860,
                "l": 1.0850,
                "c": 1.0858,
                "v": 1500,
                "n": 55,
                "t": 1598607000000,
                "vw": 1.0855,
            },
        ],
    }


def _indices_aggs_response() -> dict:
    """2 chandeliers 1min NDX (sans volume — typique indices)."""
    return {
        "status": "OK",
        "results": [
            {"o": 15000.0, "h": 15010.0, "l": 14990.0, "c": 15005.0, "t": 1598520600000},
            {"o": 15005.0, "h": 15020.0, "l": 15000.0, "c": 15015.0, "t": 1598607000000},
        ],
    }


class TestForexFetcher:
    @respx.mock
    def test_fetch_writes_dump_and_aggregates(self, forex_client, forex_settings):
        """V2SingleSymbolFetcher forex : dump + aggregate avec préfixe C:."""
        respx.get(url__regex=r"/v2/aggs/ticker/C:EURUSD/range/1/minute/.*").mock(
            return_value=httpx.Response(200, json=_forex_aggs_response())
        )

        inst = Instrument(InstrumentType.FOREX, "EURUSD")
        result = V2SingleSymbolFetcher().fetch(inst, forex_settings, forex_client)

        assert result["status"] == "ok"
        assert result["candles"] == 2
        assert aggregate_exists(inst, forex_settings)

        agg = read_aggregate(inst, forex_settings)
        assert "volume" in agg.columns
        assert 1.0850 in agg["open"].to_list()
        # ticker stocké = symbole nu (pas C:)
        assert agg["ticker"].unique().to_list() == ["EURUSD"]

    @respx.mock
    def test_query_returns_candles(self, forex_client, forex_settings):
        """query forex retourne les chandeliers agrégés."""
        respx.get(url__regex=r"/v2/aggs/ticker/C:EURUSD/range/1/minute/.*").mock(
            return_value=httpx.Response(200, json=_forex_aggs_response())
        )

        inst = Instrument(InstrumentType.FOREX, "EURUSD")
        V2SingleSymbolFetcher().fetch(inst, forex_settings, forex_client)

        df = query(inst, forex_settings, chain=None)
        assert df.height == 2
        assert "open" in df.columns

    @respx.mock
    def test_fetch_dry_run_no_api_no_files(self, forex_client, forex_settings):
        """dry-run n'appelle pas l'API et n'écrit rien."""
        aggs_route = respx.get(url__regex=r"/v2/aggs/ticker/C:EURUSD/range/1/minute/.*").mock(
            return_value=httpx.Response(200, json=_forex_aggs_response())
        )

        inst = Instrument(InstrumentType.FOREX, "EURUSD")
        result = V2SingleSymbolFetcher().fetch(
            inst, forex_settings, forex_client, dry_run=True
        )

        assert result["status"] == "dry_run"
        assert aggs_route.call_count == 0
        assert not aggregate_exists(inst, forex_settings)


class TestIndicesFetcher:
    @respx.mock
    def test_fetch_writes_dump_without_volume(self, indices_client, indices_settings):
        """V2SingleSymbolFetcher indices : dump + aggregate sans volume."""
        respx.get(url__regex=r"/v2/aggs/ticker/I:NDX/range/1/minute/.*").mock(
            return_value=httpx.Response(200, json=_indices_aggs_response())
        )

        inst = Instrument(InstrumentType.INDICES, "NDX")
        result = V2SingleSymbolFetcher().fetch(inst, indices_settings, indices_client)

        assert result["status"] == "ok"
        assert result["candles"] == 2
        assert aggregate_exists(inst, indices_settings)

        agg = read_aggregate(inst, indices_settings)
        assert "volume" not in agg.columns
        assert 15000.0 in agg["open"].to_list()
        assert agg["ticker"].unique().to_list() == ["NDX"]

    @respx.mock
    def test_query_indices_no_volume(self, indices_client, indices_settings):
        """query indices fonctionne sans colonne volume."""
        respx.get(url__regex=r"/v2/aggs/ticker/I:NDX/range/1/minute/.*").mock(
            return_value=httpx.Response(200, json=_indices_aggs_response())
        )

        inst = Instrument(InstrumentType.INDICES, "NDX")
        V2SingleSymbolFetcher().fetch(inst, indices_settings, indices_client)

        df = query(inst, indices_settings, chain=None)
        assert df.height == 2
        assert "close" in df.columns
        assert "volume" not in df.columns
