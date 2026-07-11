"""Tests du module api/aggregates.py."""

from __future__ import annotations

import httpx
import polars as pl
import pytest
import respx

from massivibe.api.aggregates import fetch_aggs
from massivibe.api.client import MassiveClient


@pytest.fixture
def client(tmp_settings):
    c = MassiveClient(tmp_settings)
    yield c
    c.close()


class TestFetchAggs:
    """Tests de fetch_aggs."""

    @respx.mock
    def test_fetch_aggs_returns_dataframe(self, client, tmp_settings, aggs_api_response):
        """fetch_aggs retourne un DataFrame Polars."""
        respx.get("/futures/v1/aggs/ESM5").mock(
            return_value=httpx.Response(200, json=aggs_api_response)
        )

        df = fetch_aggs(client, "ESM5", tmp_settings)

        assert df.height == 2
        assert "window_start" in df.columns
        assert "open" in df.columns
        assert "close" in df.columns
        assert "ticker" in df.columns

    @respx.mock
    def test_fetch_aggs_converts_window_start_ns_to_datetime(
        self, client, tmp_settings, aggs_api_response
    ):
        """window_start (nanosecondes) est converti en Datetime."""
        respx.get("/futures/v1/aggs/ESM5").mock(
            return_value=httpx.Response(200, json=aggs_api_response)
        )

        df = fetch_aggs(client, "ESM5", tmp_settings)

        # window_start doit être de type Datetime
        assert df.schema["window_start"] == pl.Datetime("ns")
        # La première valeur doit correspondre à 2025-06-01T09:30:00Z
        from datetime import datetime, timezone

        expected = datetime(2025, 6, 1, 9, 30, 0, tzinfo=timezone.utc)
        actual = df["window_start"][0]
        # Comparaison en ignorant le tz (Polars peut stocker en UTC sans tz info)
        assert actual.year == expected.year
        assert actual.month == expected.month
        assert actual.day == expected.day
        assert actual.hour == expected.hour
        assert actual.minute == expected.minute

    @respx.mock
    def test_fetch_aggs_empty_response(self, client, tmp_settings):
        """Réponse vide retourne un DataFrame vide avec le bon schéma."""
        respx.get("/futures/v1/aggs/ESM5").mock(
            return_value=httpx.Response(200, json={"results": [], "status": "OK"})
        )

        df = fetch_aggs(client, "ESM5", tmp_settings)

        assert df.is_empty()
        assert "window_start" in df.columns

    @respx.mock
    def test_fetch_aggs_with_date_range(self, client, tmp_settings, aggs_api_response):
        """fetch_aggs avec window_start_gte/lte passe les bons params."""
        route = respx.get("/futures/v1/aggs/ESM5").mock(
            return_value=httpx.Response(200, json=aggs_api_response)
        )

        fetch_aggs(
            client,
            "ESM5",
            tmp_settings,
            window_start_gte="2025-06-01",
            window_start_lte="2025-06-30",
        )

        assert route.call_count == 1
        # Vérifier que les params ont été passés
        request = respx.calls[0].request
        # httpx encode les params dans l'URL
        assert "2025-06-01" in str(request.url)
        assert "2025-06-30" in str(request.url)
