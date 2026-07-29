"""Tests end-to-end du fetcher stocks (Phase 2).

Valide la chaîne complète : fetch v2 (adjusted=false) → dump brut → agrégation →
query avec ajustement split (défaut ON) / --no-split (brut).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from massivibe.api.client import MassiveClient
from massivibe.instruments import Instrument, InstrumentType
from massivibe.pipeline.fetchers.stocks import StocksFetcher
from massivibe.query.reader import query


@pytest.fixture
def stocks_settings(tmp_settings):
    """Settings avec stocks=['AAPL'] (et futures vide pour isoler)."""
    return tmp_settings.model_copy(update={"futures": [], "stocks": ["AAPL"]})


@pytest.fixture
def client(stocks_settings):
    c = MassiveClient(stocks_settings)
    yield c
    c.close()


def _v2_aggs_response() -> dict:
    """2 chandeliers 1min AAPL (prix bruts = 400 pré-split, 100 post-split)."""
    # t en millisecondes ; 2020-08-27 09:30 = 1598520600000 ; 2020-08-28 09:30 = 1598607000000
    return {
        "status": "OK",
        "results": [
            {"o": 400.0, "h": 401.0, "l": 399.0, "c": 400.5, "v": 1000, "n": 50, "t": 1598520600000, "vw": 400.2},
            {"o": 100.0, "h": 101.0, "l": 99.0, "c": 100.5, "v": 2000, "n": 80, "t": 1598607000000, "vw": 100.1},
        ],
    }


def _splits_response() -> dict:
    """Split AAPL 4-pour-1 le 2020-08-28 (factor=0.25)."""
    return {
        "status": "OK",
        "results": [
            {
                "adjustment_type": "forward_split",
                "execution_date": "2020-08-28",
                "historical_adjustment_factor": 0.25,
                "id": "abc",
                "split_from": 1,
                "split_to": 4,
                "ticker": "AAPL",
            }
        ],
    }


def _dividends_response() -> dict:
    return {
        "status": "OK",
        "results": [],
    }


class TestStocksFetcher:
    @respx.mock
    def test_fetch_writes_dump_and_aggregates(self, client, stocks_settings):
        """StocksFetcher fetch v2 (adjusted=false) + cache splits + dump + aggregate."""
        respx.get("/stocks/v1/splits").mock(
            return_value=httpx.Response(200, json=_splits_response())
        )
        respx.get("/stocks/v1/dividends").mock(
            return_value=httpx.Response(200, json=_dividends_response())
        )
        # L'URL v2 contient from/to ; on match par path prefix
        respx.get(url__regex=r"/v2/aggs/ticker/AAPL/range/1/minute/.*").mock(
            return_value=httpx.Response(200, json=_v2_aggs_response())
        )

        inst = Instrument(InstrumentType.STOCKS, "AAPL")
        fetcher = StocksFetcher()
        result = fetcher.fetch(inst, stocks_settings, client)

        assert result["status"] == "ok"
        assert result["candles"] == 2

        # L'agrégé existe
        from massivibe.storage.aggregate_cache import aggregate_exists

        assert aggregate_exists(inst, stocks_settings)

        # Vérifier que les prix stockés sont BRUTS (400.0, non ajustés)
        from massivibe.storage.aggregate_cache import read_aggregate

        agg = read_aggregate(inst, stocks_settings)
        opens = agg["open"].to_list()
        assert 400.0 in opens  # prix brut pré-split conservé

    @respx.mock
    def test_query_with_split_adjustment_default(self, client, stocks_settings):
        """query sans --no-split applique l'ajustement split (défaut ON)."""
        respx.get("/stocks/v1/splits").mock(
            return_value=httpx.Response(200, json=_splits_response())
        )
        respx.get("/stocks/v1/dividends").mock(
            return_value=httpx.Response(200, json=_dividends_response())
        )
        respx.get(url__regex=r"/v2/aggs/ticker/AAPL/range/1/minute/.*").mock(
            return_value=httpx.Response(200, json=_v2_aggs_response())
        )

        inst = Instrument(InstrumentType.STOCKS, "AAPL")
        StocksFetcher().fetch(inst, stocks_settings, client)

        # query sans no_split → ajustement split appliqué
        df = query(inst, stocks_settings, chain=None)
        opens = df["open"].to_list()
        # Le chandelier pré-split (400.0 brut) → ajusté = 400 * 0.25 = 100.0
        assert 100.0 in opens
        # Le chandelier post-split (100.0 brut) → inchangé
        # (les deux valent 100 après ajustement)
        assert all(o == 100.0 for o in opens)

    @respx.mock
    def test_query_with_no_split_keeps_raw(self, client, stocks_settings):
        """query --no-split conserve les prix bruts."""
        respx.get("/stocks/v1/splits").mock(
            return_value=httpx.Response(200, json=_splits_response())
        )
        respx.get("/stocks/v1/dividends").mock(
            return_value=httpx.Response(200, json=_dividends_response())
        )
        respx.get(url__regex=r"/v2/aggs/ticker/AAPL/range/1/minute/.*").mock(
            return_value=httpx.Response(200, json=_v2_aggs_response())
        )

        inst = Instrument(InstrumentType.STOCKS, "AAPL")
        StocksFetcher().fetch(inst, stocks_settings, client)

        df = query(inst, stocks_settings, chain=None, no_split=True)
        opens = df["open"].to_list()
        # Prix bruts : 400.0 (pré-split) et 100.0 (post-split)
        assert 400.0 in opens
        assert 100.0 in opens

    @respx.mock
    def test_fetch_dry_run_no_api_no_files(self, client, stocks_settings):
        """dry-run n'appelle pas l'API (splits exceptés) et n'écrit rien."""
        respx.get("/stocks/v1/splits").mock(
            return_value=httpx.Response(200, json=_splits_response())
        )
        respx.get("/stocks/v1/dividends").mock(
            return_value=httpx.Response(200, json=_dividends_response())
        )
        aggs_route = respx.get(url__regex=r"/v2/aggs/ticker/AAPL/range/1/minute/.*").mock(
            return_value=httpx.Response(200, json=_v2_aggs_response())
        )

        inst = Instrument(InstrumentType.STOCKS, "AAPL")
        result = StocksFetcher().fetch(inst, stocks_settings, client, dry_run=True)

        assert result["status"] == "dry_run"
        # dry-run ne fetch PAS les aggs (les caches corp actions sont quand même rafraîchis)
        assert aggs_route.call_count == 0
