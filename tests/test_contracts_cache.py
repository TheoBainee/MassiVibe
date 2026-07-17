"""Tests du module contracts/cache.py (ContractsCache)."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from massivibe.api.client import MassiveClient
from massivibe.contracts.cache import ContractsCache
from massivibe.storage.parquet_io import read_meta


@pytest.fixture
def client(tmp_settings):
    c = MassiveClient(tmp_settings)
    yield c
    c.close()


class TestContractsCache:
    """Tests du cache des contrats par product_code."""

    @respx.mock
    def test_cache_miss_fetches_api(self, client, tmp_settings, contracts_api_response):
        """Cache absent → fetch l'API et écrit le Parquet + sidecar."""
        respx.get("/futures/v1/contracts").mock(
            return_value=httpx.Response(200, json=contracts_api_response)
        )

        cache = ContractsCache("ES", tmp_settings)
        assert not cache.exists  # cache absent au départ

        df = cache.get(client)

        assert cache.exists  # cache créé
        assert df.height == 2  # 2 contrats dans la réponse
        assert "ticker" in df.columns

        # Vérifier le sidecar .meta.json
        meta = read_meta(cache.parquet_path)
        assert meta is not None
        assert meta["product_code"] == "ES"
        assert "last_fetched_at" in meta
        assert meta["row_count"] == 2

    @respx.mock
    def test_cache_fresh_skips_api(self, client, tmp_settings, contracts_api_response):
        """Cache frais → skip l'API (pas d'appel réseau)."""
        # Premier appel : fetch l'API
        route = respx.get("/futures/v1/contracts").mock(
            return_value=httpx.Response(200, json=contracts_api_response)
        )

        cache = ContractsCache("ES", tmp_settings)
        cache.get(client)
        assert route.call_count == 1

        # Deuxième appel : cache frais → skip
        df = cache.get(client)
        assert route.call_count == 1  # pas de nouvel appel API
        assert df.height == 2

    @respx.mock
    def test_force_refresh_bypasses_cache(self, client, tmp_settings, contracts_api_response):
        """force_refresh=True → fetch l'API même si le cache est frais."""
        route = respx.get("/futures/v1/contracts").mock(
            return_value=httpx.Response(200, json=contracts_api_response)
        )

        cache = ContractsCache("ES", tmp_settings)
        cache.get(client)  # premier fetch
        assert route.call_count == 1

        cache.get(client, force_refresh=True)  # force refresh
        assert route.call_count == 2  # nouvel appel API

    @respx.mock
    def test_cache_expired_refetches(self, client, tmp_settings, contracts_api_response):
        """Cache périmé (TTL dépassé) → re-fetch l'API."""
        route = respx.get("/futures/v1/contracts").mock(
            return_value=httpx.Response(200, json=contracts_api_response)
        )

        # TTL très court (0 jour = toujours périmé)
        settings_short_ttl = tmp_settings.model_copy(update={"instrument_cache_ttl_days": 0})

        cache = ContractsCache("ES", settings_short_ttl)
        cache.get(client)  # premier fetch
        assert route.call_count == 1

        # Le cache est immédiatement périmé (TTL=0)
        cache.get(client)  # re-fetch
        assert route.call_count == 2

    def test_get_without_client_raises(self, tmp_settings):
        """get() sans client sur cache absent lève une erreur."""
        cache = ContractsCache("ES", tmp_settings)
        with pytest.raises(ValueError, match="aucun client"):
            cache.get()

    def test_get_last_fetched_none_if_absent(self, tmp_settings):
        """get_last_fetched() retourne None si le cache n'existe pas."""
        cache = ContractsCache("ES", tmp_settings)
        assert cache.get_last_fetched() is None

    @respx.mock
    def test_get_last_fetched_returns_datetime(self, client, tmp_settings, contracts_api_response):
        """get_last_fetched() retourne un datetime après un fetch."""
        respx.get("/futures/v1/contracts").mock(
            return_value=httpx.Response(200, json=contracts_api_response)
        )

        cache = ContractsCache("ES", tmp_settings)
        cache.get(client)

        last_fetched = cache.get_last_fetched()
        assert last_fetched is not None
        assert isinstance(last_fetched, datetime)
        # Vérifier que c'est récent (dans les dernières secondes)
        now = datetime.now(UTC)
        assert (now - last_fetched).total_seconds() < 10
