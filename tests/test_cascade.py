"""Tests du module pipeline/cascade.py."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import polars as pl
import pytest
import respx

from massivibe.api.client import MassiveClient
from massivibe.pipeline.cascade import (
    CascadeError,
    ensure_aggregate,
    ensure_contracts,
    ensure_raw_dumps,
    print_status_snapshot,
)
from massivibe.storage.raw_dumps import save_raw_dump


@pytest.fixture
def client(tmp_settings):
    c = MassiveClient(tmp_settings)
    yield c
    c.close()


class TestEnsureContracts:
    """Tests de ensure_contracts."""

    @respx.mock
    def test_ensure_contracts_fresh_cache_ok(self, client, tmp_settings, contracts_api_response):
        """Cache frais → pas de WARNING, pas d'appel API."""
        route = respx.get("/futures/v1/contracts").mock(
            return_value=httpx.Response(200, json=contracts_api_response)
        )

        # Premier appel : populate le cache
        from massivibe.contracts.cache import ContractsCache

        cache = ContractsCache("ES", tmp_settings)
        cache.get(client)

        calls_after_first = route.call_count
        assert calls_after_first == 1

        # ensure_contracts : cache frais → pas de nouvel appel
        ensure_contracts("ES", client, tmp_settings)
        assert route.call_count == calls_after_first  # pas de nouvel appel

    @respx.mock
    def test_ensure_contracts_no_cascade_raises(self, client, tmp_settings):
        """Cache absent + no_cascade=True → CascadeError."""
        with pytest.raises(CascadeError, match="contracts"):
            ensure_contracts("ES", client, tmp_settings, no_cascade=True)

    @respx.mock
    def test_ensure_contracts_absent_triggers_fetch(self, client, tmp_settings, contracts_api_response):
        """Cache absent + cascade → fetch automatique."""
        route = respx.get("/futures/v1/contracts").mock(
            return_value=httpx.Response(200, json=contracts_api_response)
        )

        ensure_contracts("ES", client, tmp_settings)

        assert route.call_count == 1  # fetch automatique


class TestEnsureRawDumps:
    """Tests de ensure_raw_dumps."""

    def test_ensure_raw_dumps_present_ok(self, client, tmp_settings):
        """Dumps existants → pas de cascade."""
        # Créer un dump
        df = pl.DataFrame(
            {
                "window_start": [datetime(2025, 6, 1, 9, 30, 0, tzinfo=timezone.utc)],
                "ticker": ["ESM5"],
                "open": [4500.0],
                "high": [4501.0],
                "low": [4499.0],
                "close": [4500.5],
                "volume": [100],
            }
        )
        save_raw_dump(df, "ES", "ESM5", "20260711T183000", tmp_settings)

        # ensure_raw_dumps : dumps présents → pas de cascade
        # (ne doit pas lever d'erreur)
        ensure_raw_dumps("ES", client, tmp_settings)

    def test_ensure_raw_dumps_no_cascade_raises(self, client, tmp_settings):
        """Pas de dumps + no_cascade=True → CascadeError."""
        with pytest.raises(CascadeError, match="fetch"):
            ensure_raw_dumps("ES", client, tmp_settings, no_cascade=True)


class TestEnsureAggregate:
    """Tests de ensure_aggregate."""

    @respx.mock
    def test_ensure_aggregate_present_ok(self, client, tmp_settings, contracts_api_response):
        """Agrégé existant → pas de cascade."""
        # Créer un dump + agrégé
        df = pl.DataFrame(
            {
                "window_start": [datetime(2025, 6, 1, 9, 30, 0, tzinfo=timezone.utc)],
                "ticker": ["ESM5"],
                "open": [4500.0],
                "high": [4501.0],
                "low": [4499.0],
                "close": [4500.5],
                "volume": [100],
            }
        )
        save_raw_dump(df, "ES", "ESM5", "20260711T183000", tmp_settings)

        from massivibe.pipeline.aggregator import aggregate

        aggregate("ES", tmp_settings)

        # Mocker l'API contrats (le cache contrats n'existe pas, il sera fetché)
        respx.get("/futures/v1/contracts").mock(
            return_value=httpx.Response(200, json=contracts_api_response)
        )

        # ensure_aggregate : agrégé présent → pas de cascade de aggregate
        chain = ensure_aggregate("ES", client, tmp_settings)
        assert chain is not None

    def test_ensure_aggregate_no_cascade_raises(self, client, tmp_settings):
        """Pas d'agrégé + no_cascade=True → CascadeError."""
        with pytest.raises(CascadeError, match="aggregate"):
            ensure_aggregate("ES", client, tmp_settings, no_cascade=True)


class TestPrintStatusSnapshot:
    """Tests de print_status_snapshot."""

    def test_status_snapshot_no_error(self, tmp_settings, capsys):
        """print_status_snapshot ne lève pas d'erreur même sans données."""
        # Doit juste logger sans erreur
        print_status_snapshot(["ES", "NQ"], tmp_settings)
