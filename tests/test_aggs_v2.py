"""Tests du module api/aggs_v2.py (endpoint v2 pour forex/stocks/indices/options)."""

from __future__ import annotations

import httpx
import polars as pl
import pytest
import respx

from massivibe.api.aggs_v2 import fetch_aggs_v2
from massivibe.api.client import MassiveClient
from massivibe.instruments import Instrument, InstrumentType


@pytest.fixture
def client(tmp_settings):
    c = MassiveClient(tmp_settings)
    yield c
    c.close()


def _v2_response(results: list[dict]) -> dict:
    return {"status": "OK", "results": results, "resultsCount": len(results)}


class TestFetchAggsV2:
    @respx.mock
    def test_stocks_fetch_and_normalize(self, client, tmp_settings):
        """fetch_aggs_v2 normalise les champs courts (o/h/l/c) en champs longs."""
        # AAPL 1min bars — t en millisecondes
        resp = _v2_response(
            [
                {"o": 190.0, "h": 191.0, "l": 189.5, "c": 190.5, "v": 1000, "n": 50, "t": 1717200000000, "vw": 190.2},
                {"o": 190.5, "h": 192.0, "l": 190.0, "c": 191.5, "v": 1500, "n": 75, "t": 1717200060000, "vw": 191.0},
            ]
        )
        respx.get("/v2/aggs/ticker/AAPL/range/1/minute/2024-06-01/2024-06-02").mock(
            return_value=httpx.Response(200, json=resp)
        )

        inst = Instrument(InstrumentType.STOCKS, "AAPL")
        df = fetch_aggs_v2(client, inst, tmp_settings, date_from="2024-06-01", date_to="2024-06-02")

        # Schéma canonique (champs longs)
        assert df.height == 2
        assert "window_start" in df.columns
        assert "open" in df.columns and "close" in df.columns
        assert "volume" in df.columns
        assert "transactions" in df.columns
        assert "vwap" in df.columns
        assert "dollar_volume" in df.columns
        # ticker = symbole nu
        assert df["ticker"].unique().to_list() == ["AAPL"]
        # session_end_date synthétisé
        assert "session_end_date" in df.columns
        # window_start en Datetime[ns]
        assert df.schema["window_start"] == pl.Datetime("ns")

    @respx.mock
    def test_stocks_sends_adjusted_false(self, client, tmp_settings):
        """Pour stocks, adjusted=false est envoyé (prix bruts)."""
        route = respx.get("/v2/aggs/ticker/AAPL/range/1/minute/2024-06-01/2024-06-02").mock(
            return_value=httpx.Response(200, json=_v2_response([]))
        )
        inst = Instrument(InstrumentType.STOCKS, "AAPL")
        fetch_aggs_v2(client, inst, tmp_settings, date_from="2024-06-01", date_to="2024-06-02")
        assert route.call_count == 1
        assert "adjusted=false" in str(respx.calls[0].request.url)

    @respx.mock
    def test_forex_uses_c_prefix(self, client, tmp_settings):
        """Forex utilise le préfixe C: dans l'URL."""
        route = respx.get("/v2/aggs/ticker/C:EURUSD/range/1/minute/2024-06-01/2024-06-02").mock(
            return_value=httpx.Response(200, json=_v2_response([]))
        )
        inst = Instrument(InstrumentType.FOREX, "EURUSD")
        fetch_aggs_v2(client, inst, tmp_settings, date_from="2024-06-01", date_to="2024-06-02")
        assert route.call_count == 1

    @respx.mock
    def test_indices_no_volume(self, client, tmp_settings):
        """Indices n'ont pas de volume — la colonne est absente."""
        resp = _v2_response(
            [{"o": 18000.0, "h": 18050.0, "l": 17990.0, "c": 18020.0, "t": 1717200000000}]
        )
        respx.get("/v2/aggs/ticker/I:NDX/range/1/minute/2024-06-01/2024-06-02").mock(
            return_value=httpx.Response(200, json=resp)
        )
        inst = Instrument(InstrumentType.INDICES, "NDX")
        df = fetch_aggs_v2(client, inst, tmp_settings, date_from="2024-06-01", date_to="2024-06-02")

        assert df.height == 1
        assert "volume" not in df.columns
        assert "open" in df.columns

    @respx.mock
    def test_empty_response(self, client, tmp_settings):
        respx.get("/v2/aggs/ticker/AAPL/range/1/minute/2024-06-01/2024-06-02").mock(
            return_value=httpx.Response(200, json={"results": [], "status": "OK"})
        )
        inst = Instrument(InstrumentType.STOCKS, "AAPL")
        df = fetch_aggs_v2(client, inst, tmp_settings, date_from="2024-06-01", date_to="2024-06-02")
        assert df.is_empty()

    def test_futures_rejected(self, client, tmp_settings, es_instrument):
        """fetch_aggs_v2 refuse les futures (endpoint dédié)."""
        with pytest.raises(ValueError, match="futures"):
            fetch_aggs_v2(client, es_instrument, tmp_settings)
