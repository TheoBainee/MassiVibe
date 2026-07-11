"""Tests du module query/reader.py."""

from __future__ import annotations

from datetime import datetime, timezone

import polars as pl
import pytest

from massivibe.pipeline.aggregator import aggregate
from massivibe.query.reader import (
    DATA_QUALITY_ERROR_THRESHOLD,
    DATA_QUALITY_WARNING_THRESHOLD,
    check_ticksize_accuracy_fn,
    query,
)
from massivibe.storage.raw_dumps import save_raw_dump


def _make_df(ticker: str, timestamps: list[datetime], prices: list[float], tick: float = 0.25) -> pl.DataFrame:
    """Crée un DataFrame de chandeliers avec prix multiples de tick."""
    return pl.DataFrame(
        {
            "window_start": timestamps,
            "ticker": [ticker] * len(timestamps),
            "open": prices,
            "high": [p + tick for p in prices],
            "low": [p - tick for p in prices],
            "close": [p + tick for p in prices],  # multiple de tick
            "settlement_price": [p + tick for p in prices],  # multiple de tick
            "volume": [100] * len(prices),
            "dollar_volume": [1000.0] * len(prices),
            "transactions": [10] * len(prices),
            "session_end_date": [ts.date() for ts in timestamps],
        }
    )


@pytest.fixture
def setup_aggregate(tmp_settings, sample_chain):
    """Crée un cache agrégé avec des données de test (prix conformes au tick)."""
    ts = [
        datetime(2025, 6, 1, 9, 30, 0, tzinfo=timezone.utc),
        datetime(2025, 6, 1, 9, 31, 0, tzinfo=timezone.utc),
        datetime(2025, 6, 1, 9, 32, 0, tzinfo=timezone.utc),
    ]
    prices = [4500.00, 4501.25, 4502.50]  # tous multiples de 0.25

    df = _make_df("ESM5", ts, prices, tick=0.25)
    save_raw_dump(df, "ES", "ESM5", "20260711T183000", tmp_settings)
    aggregate("ES", tmp_settings)


class TestQuery:
    """Tests de la fonction query()."""

    def test_query_returns_all_data(self, tmp_settings, sample_chain, setup_aggregate):
        """query sans filtre retourne toutes les données."""
        df = query("ES", tmp_settings, sample_chain)

        assert df.height == 3
        assert "window_start" in df.columns

    def test_query_with_start_filter(self, tmp_settings, sample_chain, setup_aggregate):
        """query avec --start filtre les données."""
        df = query(
            "ES",
            tmp_settings,
            sample_chain,
            start=datetime(2025, 6, 1, 9, 31, 0, tzinfo=timezone.utc),
        )

        assert df.height == 2  # 9:31 et 9:32

    def test_query_with_end_filter(self, tmp_settings, sample_chain, setup_aggregate):
        """query avec --end filtre les données."""
        df = query(
            "ES",
            tmp_settings,
            sample_chain,
            end=datetime(2025, 6, 1, 9, 31, 0, tzinfo=timezone.utc),
        )

        assert df.height == 2  # 9:30 et 9:31

    def test_query_with_limit(self, tmp_settings, sample_chain, setup_aggregate):
        """query avec --limit limite le nombre de lignes."""
        df = query("ES", tmp_settings, sample_chain, limit=2)

        assert df.height == 2

    def test_query_adjust_rollover_raises_not_implemented(self, tmp_settings, sample_chain, setup_aggregate):
        """adjust_rollover=True lève NotImplementedError (stub)."""
        with pytest.raises(NotImplementedError, match="non implémenté"):
            query("ES", tmp_settings, sample_chain, adjust_rollover=True)

    def test_query_normalize_and_adjust_incompatible(self, tmp_settings, sample_chain, setup_aggregate):
        """normalize_tick_size + adjust_rollover lève ValueError."""
        with pytest.raises(ValueError, match="incompatibles"):
            query(
                "ES",
                tmp_settings,
                sample_chain,
                adjust_rollover=True,
                normalize_tick_size=True,
            )


class TestNormalizeTickSize:
    """Tests de la normalisation tick_size (--normalize-tick-size)."""

    def test_normalize_converts_to_int32(self, tmp_settings, sample_chain, setup_aggregate):
        """normalize_tick_size convertit les prix en Int32."""
        df = query("ES", tmp_settings, sample_chain, normalize_tick_size=True)

        # Les colonnes de prix doivent être Int32
        assert df.schema["open"] == pl.Int32
        assert df.schema["high"] == pl.Int32
        assert df.schema["low"] == pl.Int32
        assert df.schema["close"] == pl.Int32
        assert df.schema["settlement_price"] == pl.Int32

    def test_normalize_values_correct(self, tmp_settings, sample_chain, setup_aggregate):
        """Les valeurs normalisées sont correctes (prix / tick_size)."""
        df = query("ES", tmp_settings, sample_chain, normalize_tick_size=True)

        # open de la première ligne = 4500.00 / 0.25 = 18000
        assert df["open"][0] == 18000
        # open de la deuxième ligne = 4501.25 / 0.25 = 18005
        assert df["open"][1] == 18005
        # close de la première ligne = (4500.00 + 0.25) / 0.25 = 18001
        assert df["close"][0] == 18001


class TestCheckTicksizeAccuracy:
    """Tests de --check-ticksize-accuracy (bilan de qualité)."""

    def test_check_accuracy_clean_data(self, tmp_settings, sample_chain, setup_aggregate):
        """Données 100% conformes → statut OK."""
        from massivibe.storage.aggregate_cache import read_aggregate

        df = read_aggregate("ES", tmp_settings)
        bilan = check_ticksize_accuracy_fn(df, sample_chain, trigger=0.1)

        assert bilan.height == 1  # 1 ticker
        assert bilan["ticker"][0] == "ESM5"
        assert bilan["non_conformes"][0] == 0
        assert bilan["statut"][0] == "OK"

    def test_check_accuracy_noisy_data(self, tmp_settings, sample_chain):
        """Données avec <1% non conformes → statut OK (ou ATTENTION si >=1%)."""
        # Créer des données avec 1 valeur non conforme sur 5 lignes x 5 colonnes = 1/25 = 4%
        ts = [
            datetime(2025, 6, 1, 9, 30, 0, tzinfo=timezone.utc),
            datetime(2025, 6, 1, 9, 31, 0, tzinfo=timezone.utc),
            datetime(2025, 6, 1, 9, 32, 0, tzinfo=timezone.utc),
        ]
        prices = [4500.00, 4501.25, 4502.50]
        df = _make_df("ESM5", ts, prices, tick=0.25)

        # Corrompre 1 valeur (open de la 2ème ligne = 4501.27 au lieu de 4501.25)
        df = df.with_columns(
            pl.when(pl.col("window_start") == datetime(2025, 6, 1, 9, 31, 0, tzinfo=timezone.utc))
            .then(pl.lit(4501.27))
            .otherwise(pl.col("open"))
            .alias("open")
        )

        save_raw_dump(df, "ES", "ESM5", "20260711T183000", tmp_settings)
        aggregate("ES", tmp_settings)

        from massivibe.storage.aggregate_cache import read_aggregate

        df_agg = read_aggregate("ES", tmp_settings)
        bilan = check_ticksize_accuracy_fn(df_agg, sample_chain, trigger=0.1)

        # 1 ligne non conforme sur 3 = 33% > 5% → ERREUR
        # (car la ligne entière est marquée non conforme si au moins 1 colonne l'est)
        assert bilan["non_conformes"][0] == 1
        assert bilan["statut"][0] == "ERREUR"  # 33% > 5%

    def test_check_accuracy_corrupted_data(self, tmp_settings, sample_chain):
        """Données avec >=5% non conformes → statut ERREUR."""
        ts = [
            datetime(2025, 6, 1, 9, 30, 0, tzinfo=timezone.utc),
            datetime(2025, 6, 1, 9, 31, 0, tzinfo=timezone.utc),
        ]
        # Tous les prix sont non conformes (+0.1)
        df = pl.DataFrame(
            {
                "window_start": ts,
                "ticker": ["ESM5"] * 2,
                "open": [4500.10, 4501.35],  # non multiples de 0.25
                "high": [4501.10, 4502.35],
                "low": [4499.10, 4500.35],
                "close": [4500.60, 4501.85],
                "settlement_price": [4500.60, 4501.85],
                "volume": [100, 150],
                "dollar_volume": [1000.0, 2000.0],
                "transactions": [10, 15],
                "session_end_date": [ts[0].date(), ts[1].date()],
            }
        )

        save_raw_dump(df, "ES", "ESM5", "20260711T183000", tmp_settings)
        aggregate("ES", tmp_settings)

        from massivibe.storage.aggregate_cache import read_aggregate

        df_agg = read_aggregate("ES", tmp_settings)
        bilan = check_ticksize_accuracy_fn(df_agg, sample_chain, trigger=0.1)

        assert bilan["non_conformes"][0] == 2  # toutes les lignes non conformes
        assert bilan["statut"][0] == "ERREUR"

    def test_check_accuracy_does_not_modify_data(self, tmp_settings, sample_chain, setup_aggregate):
        """check_ticksize_accuracy ne modifie pas les données (read-only)."""
        from massivibe.storage.aggregate_cache import read_aggregate

        df_before = read_aggregate("ES", tmp_settings)

        # Appeler query avec check_ticksize_accuracy
        df = query("ES", tmp_settings, sample_chain, check_ticksize_accuracy=True)

        # Les données retournées doivent être en Float64 (pas normalisées)
        assert df.schema["open"] == pl.Float64

        # Le cache agrégé ne doit pas avoir changé
        df_after = read_aggregate("ES", tmp_settings)
        assert df_after["open"].to_list() == df_before["open"].to_list()
