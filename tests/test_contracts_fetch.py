"""Tests du module api/contracts.py."""

from __future__ import annotations

import httpx
import polars as pl
import pytest
import respx

from myquantstore.api.client import MassiveClient
from myquantstore.api.contracts import fetch_contracts


@pytest.fixture
def client(tmp_settings):
    c = MassiveClient(tmp_settings)
    yield c
    c.close()


class TestFetchContracts:
    """Tests de fetch_contracts."""

    @respx.mock
    def test_fetch_contracts_returns_dataframe(self, client, tmp_settings, contracts_api_response):
        """fetch_contracts retourne un DataFrame Polars."""
        respx.get("/futures/v1/contracts").mock(
            return_value=httpx.Response(200, json=contracts_api_response)
        )

        df = fetch_contracts(client, "ES", tmp_settings)

        assert df.height == 2
        assert "ticker" in df.columns
        assert "first_trade_date" in df.columns
        assert "trade_tick_size" in df.columns

    @respx.mock
    def test_fetch_contracts_converts_dates(self, client, tmp_settings, contracts_api_response):
        """Les dates string (YYYY-MM-DD) sont converties en type Date."""
        respx.get("/futures/v1/contracts").mock(
            return_value=httpx.Response(200, json=contracts_api_response)
        )

        df = fetch_contracts(client, "ES", tmp_settings)

        assert df.schema["first_trade_date"] == pl.Date
        assert df.schema["last_trade_date"] == pl.Date

    @respx.mock
    def test_fetch_contracts_sorted_by_first_trade_date(
        self, client, tmp_settings, contracts_api_response
    ):
        """Les contrats sont triés par first_trade_date ascendant."""
        respx.get("/futures/v1/contracts").mock(
            return_value=httpx.Response(200, json=contracts_api_response)
        )

        df = fetch_contracts(client, "ES", tmp_settings)

        dates = df["first_trade_date"].to_list()
        assert dates == sorted(dates)

    @respx.mock
    def test_fetch_contracts_empty(self, client, tmp_settings):
        """Réponse vide retourne un DataFrame vide."""
        respx.get("/futures/v1/contracts").mock(
            return_value=httpx.Response(200, json={"results": [], "status": "OK"})
        )

        df = fetch_contracts(client, "ES", tmp_settings)

        assert df.is_empty()

    @respx.mock
    def test_fetch_contracts_pagination(self, client, tmp_settings):
        """La pagination concatène les résultats de plusieurs pages."""
        # Utiliser side_effect pour retourner séquentiellement les pages
        respx.get(host="api.test.massive.com", path="/futures/v1/contracts").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "ticker": "ESH5",
                                "first_trade_date": "2024-12-16",
                                "last_trade_date": "2025-03-14",
                                "settlement_date": "2025-03-14",
                                "trade_tick_size": 0.25,
                                "type": "single",
                                "product_code": "ES",
                            }
                        ],
                        "next_url": "https://api.test.massive.com/futures/v1/contracts?cursor=p2",
                        "status": "OK",
                    },
                ),
                httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "ticker": "ESM5",
                                "first_trade_date": "2025-03-17",
                                "last_trade_date": "2025-06-13",
                                "settlement_date": "2025-06-13",
                                "trade_tick_size": 0.25,
                                "type": "single",
                                "product_code": "ES",
                            }
                        ],
                        "next_url": None,
                        "status": "OK",
                    },
                ),
            ]
        )

        df = fetch_contracts(client, "ES", tmp_settings)

        assert df.height == 2
        assert df["ticker"].to_list() == ["ESH5", "ESM5"]
