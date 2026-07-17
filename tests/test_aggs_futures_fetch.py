"""Tests du module api/aggs_futures.py."""

from __future__ import annotations

from datetime import UTC

import httpx
import polars as pl
import pytest
import respx

from massivibe.api.aggs_futures import fetch_aggs_futures
from massivibe.api.client import MassiveClient


@pytest.fixture
def client(tmp_settings):
    c = MassiveClient(tmp_settings)
    yield c
    c.close()


class TestFetchAggsFutures:
    """Tests de fetch_aggs_futures."""

    @respx.mock
    def test_fetch_returns_dataframe(self, client, tmp_settings, aggs_api_response):
        """fetch_aggs_futures retourne un DataFrame Polars."""
        respx.get("/futures/v1/aggs/ESM5").mock(
            return_value=httpx.Response(200, json=aggs_api_response)
        )

        df = fetch_aggs_futures(client, "ESM5", tmp_settings)

        assert df.height == 2
        assert "window_start" in df.columns
        assert "open" in df.columns
        assert "close" in df.columns
        assert "ticker" in df.columns

    @respx.mock
    def test_fetch_converts_window_start_ns_to_datetime(self, client, tmp_settings, aggs_api_response):
        """window_start (nanosecondes) est converti en Datetime."""
        respx.get("/futures/v1/aggs/ESM5").mock(
            return_value=httpx.Response(200, json=aggs_api_response)
        )

        df = fetch_aggs_futures(client, "ESM5", tmp_settings)

        assert df.schema["window_start"] == pl.Datetime("ns")
        from datetime import datetime

        expected = datetime(2025, 6, 1, 9, 30, 0, tzinfo=UTC)
        actual = df["window_start"][0]
        assert actual.year == expected.year
        assert actual.month == expected.month
        assert actual.day == expected.day
        assert actual.hour == expected.hour
        assert actual.minute == expected.minute

    @respx.mock
    def test_fetch_empty_response(self, client, tmp_settings):
        """Réponse vide retourne un DataFrame vide avec le bon schéma."""
        respx.get("/futures/v1/aggs/ESM5").mock(
            return_value=httpx.Response(200, json={"results": [], "status": "OK"})
        )

        df = fetch_aggs_futures(client, "ESM5", tmp_settings)

        assert df.is_empty()
        assert "window_start" in df.columns

    @respx.mock
    def test_fetch_with_date_range(self, client, tmp_settings, aggs_api_response):
        """fetch avec window_start_gte/lte passe les bons params."""
        route = respx.get("/futures/v1/aggs/ESM5").mock(
            return_value=httpx.Response(200, json=aggs_api_response)
        )

        fetch_aggs_futures(
            client,
            "ESM5",
            tmp_settings,
            window_start_gte="2025-06-01",
            window_start_lte="2025-06-30",
        )

        assert route.call_count == 1
        request = respx.calls[0].request
        assert "2025-06-01" in str(request.url)
        assert "2025-06-30" in str(request.url)
