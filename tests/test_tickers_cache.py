"""Tests du module tickers/cache.py (AllTickersCache + TickerTypesCache)."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import polars as pl
import pytest
import respx

from massivibe.api.client import MassiveClient
from massivibe.storage.parquet_io import read_meta
from massivibe.tickers.cache import AllTickersCache, TickerTypesCache


@pytest.fixture
def client(tmp_settings):
    c = MassiveClient(tmp_settings)
    yield c
    c.close()


class TestAllTickersCache:
    """Tests du cache all-tickers."""

    @respx.mock
    def test_cache_miss_fetches_api(self, client, tmp_settings, all_tickers_api_response):
        """Cache absent → fetch l'API et écrit le Parquet + sidecar."""
        respx.get("/v3/reference/tickers").mock(
            return_value=httpx.Response(200, json=all_tickers_api_response)
        )

        cache = AllTickersCache(tmp_settings, market="stocks")
        assert not cache.exists

        df = cache.get(client)

        assert cache.exists
        assert df.height == 3

        meta = read_meta(cache.parquet_path)
        assert meta is not None
        assert meta["market_filter"] == "stocks"
        assert "last_fetched_at" in meta
        assert meta["row_count"] == 3

    @respx.mock
    def test_cache_fresh_skips_api(self, client, tmp_settings, all_tickers_api_response):
        """Cache frais → skip l'API (pas d'appel réseau)."""
        route = respx.get("/v3/reference/tickers").mock(
            return_value=httpx.Response(200, json=all_tickers_api_response)
        )

        cache = AllTickersCache(tmp_settings, market="stocks")
        cache.get(client)
        assert route.call_count == 1

        # Deuxième appel : cache frais → skip
        df = cache.get(client)
        assert route.call_count == 1
        assert df.height == 3

    @respx.mock
    def test_force_refresh_bypasses_cache(self, client, tmp_settings, all_tickers_api_response):
        """force_refresh=True → fetch l'API même si le cache est frais."""
        route = respx.get("/v3/reference/tickers").mock(
            return_value=httpx.Response(200, json=all_tickers_api_response)
        )

        cache = AllTickersCache(tmp_settings, market="stocks")
        cache.get(client)
        assert route.call_count == 1

        cache.get(client, force_refresh=True)
        assert route.call_count == 2

    @respx.mock
    def test_cache_expired_refetches(self, client, tmp_settings, all_tickers_api_response):
        """Cache périmé (TTL dépassé) → re-fetch l'API."""
        route = respx.get("/v3/reference/tickers").mock(
            return_value=httpx.Response(200, json=all_tickers_api_response)
        )

        settings_short_ttl = tmp_settings.model_copy(update={"instrument_cache_ttl_days": 0})

        cache = AllTickersCache(settings_short_ttl, market="stocks")
        cache.get(client)
        assert route.call_count == 1

        # TTL=0 → immédiatement périmé
        cache.get(client)
        assert route.call_count == 2

    def test_get_without_client_raises(self, tmp_settings):
        """get() sans client sur cache absent lève une erreur."""
        cache = AllTickersCache(tmp_settings, market="stocks")
        with pytest.raises(ValueError, match="aucun client"):
            cache.get()

    @respx.mock
    def test_market_mismatch_not_fresh(self, client, tmp_settings, all_tickers_api_response):
        """Un cache market=stocks n'est pas frais si on demande market=None."""
        respx.get("/v3/reference/tickers").mock(
            return_value=httpx.Response(200, json=all_tickers_api_response)
        )

        # Remplir le cache pour market=stocks
        cache_stocks = AllTickersCache(tmp_settings, market="stocks")
        cache_stocks.get(client)
        assert cache_stocks._is_fresh()

        # Demander market=None : le cache stocks n'est pas frais (périmètre différent)
        cache_all = AllTickersCache(tmp_settings, market=None)
        assert not cache_all._is_fresh()

    @respx.mock
    def test_get_last_fetched_returns_datetime(self, client, tmp_settings, all_tickers_api_response):
        """get_last_fetched() retourne un datetime après un fetch."""
        respx.get("/v3/reference/tickers").mock(
            return_value=httpx.Response(200, json=all_tickers_api_response)
        )

        cache = AllTickersCache(tmp_settings, market="stocks")
        cache.get(client)

        last_fetched = cache.get_last_fetched()
        assert last_fetched is not None
        assert isinstance(last_fetched, datetime)
        now = datetime.now(UTC)
        assert (now - last_fetched).total_seconds() < 10

    def test_get_last_fetched_none_if_absent(self, tmp_settings):
        """get_last_fetched() retourne None si le cache n'existe pas."""
        cache = AllTickersCache(tmp_settings, market="stocks")
        assert cache.get_last_fetched() is None


class TestTickerTypesCache:
    """Tests du cache ticker-types."""

    @respx.mock
    def test_cache_miss_fetches_api(self, client, tmp_settings, ticker_types_api_response):
        """Cache absent → fetch l'API et écrit le Parquet + sidecar."""
        respx.get("/v3/reference/tickers/types").mock(
            return_value=httpx.Response(200, json=ticker_types_api_response)
        )

        cache = TickerTypesCache(tmp_settings)
        assert not cache.exists

        df = cache.get(client)

        assert cache.exists
        assert df.height == 4

        meta = read_meta(cache.parquet_path)
        assert meta is not None
        assert "last_fetched_at" in meta

    @respx.mock
    def test_cache_fresh_skips_api(self, client, tmp_settings, ticker_types_api_response):
        """Cache frais → skip l'API."""
        route = respx.get("/v3/reference/tickers/types").mock(
            return_value=httpx.Response(200, json=ticker_types_api_response)
        )

        cache = TickerTypesCache(tmp_settings)
        cache.get(client)
        assert route.call_count == 1

        cache.get(client)
        assert route.call_count == 1

    @respx.mock
    def test_force_refresh_bypasses_cache(self, client, tmp_settings, ticker_types_api_response):
        """force_refresh=True → fetch même si cache frais."""
        route = respx.get("/v3/reference/tickers/types").mock(
            return_value=httpx.Response(200, json=ticker_types_api_response)
        )

        cache = TickerTypesCache(tmp_settings)
        cache.get(client)
        assert route.call_count == 1

        cache.get(client, force_refresh=True)
        assert route.call_count == 2

    def test_get_without_client_raises(self, tmp_settings):
        """get() sans client sur cache absent lève une erreur."""
        cache = TickerTypesCache(tmp_settings)
        with pytest.raises(ValueError, match="aucun client"):
            cache.get()
