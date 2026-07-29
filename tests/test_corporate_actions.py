"""Tests du module api/corporate_actions.py et corporate_actions/cache.py."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import polars as pl
import pytest
import respx

from massivibe.api.client import MassiveClient
from massivibe.api.corporate_actions import fetch_dividends, fetch_splits
from massivibe.corporate_actions.cache import CorporateActionsCache
from massivibe.storage.parquet_io import read_meta


@pytest.fixture
def client(tmp_settings):
    c = MassiveClient(tmp_settings)
    yield c
    c.close()


def _splits_response() -> dict:
    """Réponse simulée de /stocks/v1/splits (AAPL 4-for-1 2020-08-28)."""
    return {
        "status": "OK",
        "results": [
            {
                "adjustment_type": "forward_split",
                "execution_date": "2020-08-28",
                "historical_adjustment_factor": 0.25,
                "id": "abc123",
                "split_from": 1,
                "split_to": 4,
                "ticker": "AAPL",
            }
        ],
    }


def _dividends_response() -> dict:
    """Réponse simulée de /stocks/v1/dividends."""
    return {
        "status": "OK",
        "results": [
            {
                "cash_amount": 0.26,
                "currency": "USD",
                "declaration_date": "2025-07-31",
                "distribution_type": "recurring",
                "ex_dividend_date": "2025-08-11",
                "frequency": 4,
                "historical_adjustment_factor": 0.997899,
                "id": "div123",
                "pay_date": "2025-08-14",
                "record_date": "2025-08-11",
                "ticker": "AAPL",
            }
        ],
    }


class TestFetchSplits:
    @respx.mock
    def test_fetch_splits_returns_dataframe(self, client, tmp_settings):
        respx.get("/stocks/v1/splits").mock(
            return_value=httpx.Response(200, json=_splits_response())
        )
        df = fetch_splits(client, "AAPL", tmp_settings)

        assert df.height == 1
        assert "execution_date" in df.columns
        assert df.schema["execution_date"] == pl.Date
        assert df["historical_adjustment_factor"][0] == 0.25

    @respx.mock
    def test_fetch_splits_empty(self, client, tmp_settings):
        respx.get("/stocks/v1/splits").mock(
            return_value=httpx.Response(200, json={"results": [], "status": "OK"})
        )
        df = fetch_splits(client, "NOXXX", tmp_settings)
        assert df.is_empty()


class TestFetchDividends:
    @respx.mock
    def test_fetch_dividends(self, client, tmp_settings):
        """fetch_dividends retourne un DataFrame (similaire splits)."""
        respx.get("/stocks/v1/dividends").mock(
            return_value=httpx.Response(200, json=_dividends_response())
        )
        df = fetch_dividends(client, "AAPL", tmp_settings)
        assert df.height == 1
        assert "ex_dividend_date" in df.columns
        assert "historical_adjustment_factor" in df.columns


class TestCorporateActionsCache:
    @respx.mock
    def test_cache_miss_fetches_api(self, client, tmp_settings):
        respx.get("/stocks/v1/splits").mock(
            return_value=httpx.Response(200, json=_splits_response())
        )

        cache = CorporateActionsCache("AAPL", "splits", tmp_settings)
        assert not cache.exists

        df = cache.get(client)

        assert cache.exists
        assert df.height == 1
        meta = read_meta(cache.parquet_path)
        assert meta is not None
        assert meta["ticker"] == "AAPL"
        assert meta["kind"] == "splits"
        assert "last_fetched_at" in meta

    @respx.mock
    def test_cache_fresh_skips_api(self, client, tmp_settings):
        route = respx.get("/stocks/v1/splits").mock(
            return_value=httpx.Response(200, json=_splits_response())
        )

        cache = CorporateActionsCache("AAPL", "splits", tmp_settings)
        cache.get(client)
        assert route.call_count == 1

        # 2e appel : cache frais → skip
        cache.get(client)
        assert route.call_count == 1

    @respx.mock
    def test_force_refresh(self, client, tmp_settings):
        route = respx.get("/stocks/v1/splits").mock(
            return_value=httpx.Response(200, json=_splits_response())
        )

        cache = CorporateActionsCache("AAPL", "splits", tmp_settings)
        cache.get(client)
        cache.get(client, force_refresh=True)
        assert route.call_count == 2

    def test_get_without_client_raises(self, tmp_settings):
        cache = CorporateActionsCache("AAPL", "splits", tmp_settings)
        with pytest.raises(ValueError, match="aucun client"):
            cache.get()

    @respx.mock
    def test_cache_dividends_miss_fetches_api(self, client, tmp_settings):
        respx.get("/stocks/v1/dividends").mock(
            return_value=httpx.Response(200, json=_dividends_response())
        )

        cache = CorporateActionsCache("AAPL", "dividends", tmp_settings)
        assert not cache.exists

        df = cache.get(client)

        assert cache.exists
        assert df.height == 1
        assert "ex_dividend_date" in df.columns
        meta = read_meta(cache.parquet_path)
        assert meta is not None
        assert meta["kind"] == "dividends"

    def test_get_last_fetched_none_if_absent(self, tmp_settings):
        cache = CorporateActionsCache("AAPL", "splits", tmp_settings)
        assert cache.get_last_fetched() is None

    @respx.mock
    def test_get_last_fetched_returns_datetime(self, client, tmp_settings):
        respx.get("/stocks/v1/splits").mock(
            return_value=httpx.Response(200, json=_splits_response())
        )
        cache = CorporateActionsCache("AAPL", "splits", tmp_settings)
        cache.get(client)
        last = cache.get_last_fetched()
        assert last is not None
        assert isinstance(last, datetime)
        assert (datetime.now(UTC) - last).total_seconds() < 10
