"""Tests du module api/tickers.py (fetch_all_tickers + fetch_ticker_types)."""

from __future__ import annotations

import httpx
import polars as pl
import pytest
import respx

from massivibe.api.client import MassiveClient
from massivibe.api.tickers import fetch_all_tickers, fetch_ticker_types


@pytest.fixture
def client(tmp_settings):
    c = MassiveClient(tmp_settings)
    yield c
    c.close()


class TestFetchAllTickers:
    """Tests de fetch_all_tickers."""

    @respx.mock
    def test_returns_dataframe(self, client, tmp_settings, all_tickers_api_response):
        """fetch_all_tickers retourne un DataFrame Polars avec les bonnes colonnes."""
        respx.get("/v3/reference/tickers").mock(
            return_value=httpx.Response(200, json=all_tickers_api_response)
        )

        df = fetch_all_tickers(client, tmp_settings, market="stocks")

        assert df.height == 3
        assert "ticker" in df.columns
        assert "name" in df.columns
        assert "market" in df.columns
        assert "type" in df.columns
        assert "active" in df.columns

    @respx.mock
    def test_default_market_is_stocks(self, client, tmp_settings, all_tickers_api_response):
        """Par défaut, market='stocks' est passé à l'API."""
        route = respx.get("/v3/reference/tickers").mock(
            return_value=httpx.Response(200, json=all_tickers_api_response)
        )

        fetch_all_tickers(client, tmp_settings)

        # Le param market=stocks doit être dans la requête
        request = route.calls[0].request
        assert "market=stocks" in str(request.url)

    @respx.mock
    def test_market_none_omits_param(self, client, tmp_settings, all_tickers_api_response):
        """market=None omet le paramètre market (tous les marchés)."""
        route = respx.get("/v3/reference/tickers").mock(
            return_value=httpx.Response(200, json=all_tickers_api_response)
        )

        fetch_all_tickers(client, tmp_settings, market=None)

        request = route.calls[0].request
        assert "market=" not in str(request.url)

    @respx.mock
    def test_converts_dates_to_pl_date(self, client, tmp_settings, all_tickers_api_response):
        """Les champs date (last_updated_utc) sont convertis en pl.Date."""
        respx.get("/v3/reference/tickers").mock(
            return_value=httpx.Response(200, json=all_tickers_api_response)
        )

        df = fetch_all_tickers(client, tmp_settings)

        assert "last_updated_utc" in df.columns
        assert df.schema["last_updated_utc"] == pl.Date

    @respx.mock
    def test_sorted_by_ticker(self, client, tmp_settings, all_tickers_api_response):
        """Les tickers sont triés par ticker ascendant."""
        respx.get("/v3/reference/tickers").mock(
            return_value=httpx.Response(200, json=all_tickers_api_response)
        )

        df = fetch_all_tickers(client, tmp_settings)

        tickers = df["ticker"].to_list()
        assert tickers == sorted(tickers)

    @respx.mock
    def test_empty_response(self, client, tmp_settings):
        """Réponse vide retourne un DataFrame vide."""
        respx.get("/v3/reference/tickers").mock(
            return_value=httpx.Response(200, json={"results": [], "status": "OK"})
        )

        df = fetch_all_tickers(client, tmp_settings)

        assert df.is_empty()

    @respx.mock
    def test_pagination(self, client, tmp_settings):
        """La pagination concatène les résultats de plusieurs pages via next_url."""
        respx.get(host="api.test.massive.com", path="/v3/reference/tickers").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "results": [
                            {"ticker": "AAA", "name": "A Corp", "market": "stocks", "type": "CS", "active": True},
                        ],
                        "next_url": "https://api.test.massive.com/v3/reference/tickers?cursor=p2",
                        "status": "OK",
                    },
                ),
                httpx.Response(
                    200,
                    json={
                        "results": [
                            {"ticker": "BBB", "name": "B Corp", "market": "stocks", "type": "CS", "active": True},
                        ],
                        "status": "OK",
                    },
                ),
            ],
        )

        df = fetch_all_tickers(client, tmp_settings)

        assert df.height == 2
        assert set(df["ticker"].to_list()) == {"AAA", "BBB"}

    def test_invalid_market_raises(self, client, tmp_settings):
        """Un marché invalide lève ValueError avant tout appel API."""
        with pytest.raises(ValueError, match="Marché 'foo' invalide"):
            fetch_all_tickers(client, tmp_settings, market="foo")

    @respx.mock
    def test_active_filter_passed(self, client, tmp_settings, all_tickers_api_response):
        """active=True est transmis à l'API."""
        route = respx.get("/v3/reference/tickers").mock(
            return_value=httpx.Response(200, json=all_tickers_api_response)
        )

        fetch_all_tickers(client, tmp_settings, active=False)

        request = route.calls[0].request
        assert "active=false" in str(request.url)


class TestFetchTickerTypes:
    """Tests de fetch_ticker_types."""

    @respx.mock
    def test_returns_dataframe(self, client, tmp_settings, ticker_types_api_response):
        """fetch_ticker_types retourne un DataFrame avec asset_class/code/description/locale."""
        respx.get("/v3/reference/tickers/types").mock(
            return_value=httpx.Response(200, json=ticker_types_api_response)
        )

        df = fetch_ticker_types(client, tmp_settings)

        assert df.height == 4
        assert "asset_class" in df.columns
        assert "code" in df.columns
        assert "description" in df.columns
        assert "locale" in df.columns

    @respx.mock
    def test_sorted_by_asset_class_then_code(self, client, tmp_settings, ticker_types_api_response):
        """Les types sont triés par asset_class puis code."""
        respx.get("/v3/reference/tickers/types").mock(
            return_value=httpx.Response(200, json=ticker_types_api_response)
        )

        df = fetch_ticker_types(client, tmp_settings)

        # Vérifie que le tri est déterministe (asset_class asc, code asc)
        pairs = list(df.select("asset_class", "code").iter_rows())
        assert pairs == sorted(pairs)

    @respx.mock
    def test_empty_response(self, client, tmp_settings):
        """Réponse vide retourne un DataFrame vide."""
        respx.get("/v3/reference/tickers/types").mock(
            return_value=httpx.Response(200, json={"results": [], "status": "OK"})
        )

        df = fetch_ticker_types(client, tmp_settings)

        assert df.is_empty()
