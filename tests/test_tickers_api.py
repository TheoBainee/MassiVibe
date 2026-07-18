"""Tests de massivibe.api.tickers."""

from __future__ import annotations

import httpx
import pytest
import respx

from massivibe.api.client import MassiveClient
from massivibe.api.tickers import fetch_all_tickers, fetch_ticker_types


@respx.mock
def test_fetch_all_tickers_paginated(tmp_settings):
    """Pagination next_url concatène les pages."""
    base = "https://api.test.massive.com"
    next_url = f"{base}/v3/reference/tickers?cursor=abc"
    page1 = {
        "results": [
            {
                "ticker": "A",
                "name": "Agilent",
                "market": "stocks",
                "type": "CS",
                "active": True,
                "primary_exchange": "XNYS",
            }
        ],
        "next_url": next_url,
        "status": "OK",
    }
    page2 = {
        "results": [
            {
                "ticker": "AAPL",
                "name": "Apple Inc",
                "market": "stocks",
                "type": "CS",
                "active": True,
                "primary_exchange": "XNAS",
            }
        ],
        "status": "OK",
    }
    respx.get(url__regex=r".*/v3/reference/tickers.*").mock(
        side_effect=[
            httpx.Response(200, json=page1),
            httpx.Response(200, json=page2),
        ]
    )

    with MassiveClient(tmp_settings) as client:
        df = fetch_all_tickers(client, tmp_settings, market="stocks", active=True)

    assert df.height == 2
    assert set(df["ticker"].to_list()) == {"A", "AAPL"}
    assert "market" in df.columns
    assert "name" in df.columns


@respx.mock
def test_fetch_all_tickers_empty(tmp_settings):
    respx.get("https://api.test.massive.com/v3/reference/tickers").mock(
        return_value=httpx.Response(200, json={"results": [], "status": "OK"})
    )
    with MassiveClient(tmp_settings) as client:
        df = fetch_all_tickers(client, tmp_settings)
    assert df.is_empty()
    assert "ticker" in df.columns


@respx.mock
def test_fetch_ticker_types(tmp_settings):
    respx.get("https://api.test.massive.com/v3/reference/tickers/types").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "code": "CS",
                        "description": "Common Stock",
                        "asset_class": "stocks",
                        "locale": "us",
                    },
                    {
                        "code": "ETF",
                        "description": "Exchange Traded Fund",
                        "asset_class": "stocks",
                        "locale": "us",
                    },
                ],
                "status": "OK",
            },
        )
    )
    with MassiveClient(tmp_settings) as client:
        df = fetch_ticker_types(client)
    assert df.height == 2
    assert "CS" in df["code"].to_list()
