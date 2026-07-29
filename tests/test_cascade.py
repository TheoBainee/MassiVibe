"""Tests du module pipeline/cascade.py."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import polars as pl
import pytest
import respx

from myquantstore.api.client import MassiveClient
from myquantstore.pipeline.cascade import (
    CascadeError,
    ensure_aggregate,
    ensure_pre_fetch,
    ensure_raw_dumps,
    print_status_snapshot,
)
from myquantstore.storage.raw_dumps import save_raw_dump


@pytest.fixture
def client(tmp_settings):
    c = MassiveClient(tmp_settings)
    yield c
    c.close()


class TestEnsurePreFetch:
    """Tests de ensure_pre_fetch (cache de listing adapté au type)."""

    @respx.mock
    def test_ensure_pre_fetch_fresh_cache_ok(self, client, tmp_settings, es_instrument, contracts_api_response):
        """Cache contrats frais → pas de WARNING, pas d'appel API."""
        route = respx.get("/futures/v1/contracts").mock(
            return_value=httpx.Response(200, json=contracts_api_response)
        )

        from myquantstore.contracts.cache import ContractsCache

        cache = ContractsCache("ES", tmp_settings)
        cache.get(client)
        assert route.call_count == 1

        # ensure_pre_fetch : cache frais → pas de nouvel appel
        ensure_pre_fetch(es_instrument, client, tmp_settings)
        assert route.call_count == 1

    @respx.mock
    def test_ensure_pre_fetch_no_cascade_raises(self, client, tmp_settings, es_instrument):
        """Cache absent + no_cascade=True → CascadeError."""
        with pytest.raises(CascadeError, match="contracts"):
            ensure_pre_fetch(es_instrument, client, tmp_settings, no_cascade=True)

    @respx.mock
    def test_ensure_pre_fetch_absent_triggers_fetch(self, client, tmp_settings, es_instrument, contracts_api_response):
        """Cache absent + cascade → fetch automatique."""
        route = respx.get("/futures/v1/contracts").mock(
            return_value=httpx.Response(200, json=contracts_api_response)
        )

        ensure_pre_fetch(es_instrument, client, tmp_settings)

        assert route.call_count == 1


class TestEnsureRawDumps:
    """Tests de ensure_raw_dumps."""

    def test_ensure_raw_dumps_present_ok(self, client, tmp_settings, es_instrument):
        """Dumps existants → pas de cascade."""
        df = pl.DataFrame(
            {
                "window_start": [datetime(2025, 6, 1, 9, 30, 0, tzinfo=UTC)],
                "ticker": ["ESM5"],
                "open": [4500.0],
                "high": [4501.0],
                "low": [4499.0],
                "close": [4500.5],
                "volume": [100],
            }
        )
        save_raw_dump(df, es_instrument, "ESM5", "20260711T183000", tmp_settings)

        ensure_raw_dumps(es_instrument, client, tmp_settings)

    def test_ensure_raw_dumps_no_cascade_raises(self, client, tmp_settings, es_instrument):
        """Pas de dumps + no_cascade=True → CascadeError."""
        with pytest.raises(CascadeError, match="fetch"):
            ensure_raw_dumps(es_instrument, client, tmp_settings, no_cascade=True)


class TestEnsureAggregate:
    """Tests de ensure_aggregate."""

    @respx.mock
    def test_ensure_aggregate_present_ok(self, client, tmp_settings, es_instrument, contracts_api_response):
        """Agrégé existant → pas de cascade."""
        df = pl.DataFrame(
            {
                "window_start": [datetime(2025, 6, 1, 9, 30, 0, tzinfo=UTC)],
                "ticker": ["ESM5"],
                "open": [4500.0],
                "high": [4501.0],
                "low": [4499.0],
                "close": [4500.5],
                "volume": [100],
            }
        )
        save_raw_dump(df, es_instrument, "ESM5", "20260711T183000", tmp_settings)

        from myquantstore.pipeline.aggregator import aggregate

        aggregate(es_instrument, tmp_settings)

        respx.get("/futures/v1/contracts").mock(
            return_value=httpx.Response(200, json=contracts_api_response)
        )

        chain = ensure_aggregate(es_instrument, client, tmp_settings)
        assert chain is not None

    def test_ensure_aggregate_no_cascade_raises(self, client, tmp_settings, es_instrument):
        """Pas d'agrégé + no_cascade=True → CascadeError."""
        with pytest.raises(CascadeError, match="aggregate"):
            ensure_aggregate(es_instrument, client, tmp_settings, no_cascade=True)


class TestPrintStatusSnapshot:
    """Tests de print_status_snapshot."""

    def test_status_snapshot_no_error(self, tmp_settings, es_instrument, nq_instrument, capsys):
        """print_status_snapshot ne lève pas d'erreur même sans données."""
        print_status_snapshot([es_instrument, nq_instrument], tmp_settings)
