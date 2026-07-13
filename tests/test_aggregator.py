"""Tests du module pipeline/aggregator.py."""

from __future__ import annotations

from datetime import datetime, timezone

import polars as pl
import pytest

from massivibe.pipeline.aggregator import aggregate
from massivibe.storage.aggregate_cache import aggregate_exists, read_aggregate
from massivibe.storage.raw_dumps import save_raw_dump


def _make_df(ticker: str, timestamps: list[datetime], prices: list[float]) -> pl.DataFrame:
    """Crée un DataFrame de chandeliers pour les tests."""
    return pl.DataFrame(
        {
            "window_start": timestamps,
            "ticker": [ticker] * len(timestamps),
            "open": prices,
            "high": [p + 1 for p in prices],
            "low": [p - 1 for p in prices],
            "close": [p + 0.5 for p in prices],
            "settlement_price": [p + 0.5 for p in prices],
            "volume": [100] * len(prices),
            "dollar_volume": [1000.0] * len(prices),
            "transactions": [10] * len(prices),
            "session_end_date": [ts.date() for ts in timestamps],
        }
    )


class TestAggregator:
    """Tests de l'agrégation (fusion + dedup + cast Categorical)."""

    def test_aggregate_single_dump(self, tmp_settings):
        """Agréger un seul dump produit un cache avec les mêmes données."""
        ts = datetime(2025, 6, 1, 9, 30, 0, tzinfo=timezone.utc)
        df = _make_df("ESM5", [ts], [4500.0])

        save_raw_dump(df, "ES", "ESM5", "20260711T183000", tmp_settings)

        result = aggregate("ES", tmp_settings)

        assert result.height == 1
        assert aggregate_exists("ES", tmp_settings)

    def test_aggregate_dedup_overlapping_dumps(self, tmp_settings):
        """Deux dumps chevauchants → dédup sur (window_start, ticker) keep=last."""
        ts1 = datetime(2025, 6, 1, 9, 30, 0, tzinfo=timezone.utc)
        ts2 = datetime(2025, 6, 1, 9, 31, 0, tzinfo=timezone.utc)

        # Dump 1 : 2 chandeliers
        df1 = _make_df("ESM5", [ts1, ts2], [4500.0, 4501.0])
        save_raw_dump(df1, "ES", "ESM5", "20260704T180000", tmp_settings)

        # Dump 2 : même ts1 mais prix différent (re-fetch avec buffer)
        df2 = _make_df("ESM5", [ts1], [4502.0])
        save_raw_dump(df2, "ES", "ESM5", "20260711T183000", tmp_settings)

        result = aggregate("ES", tmp_settings)

        # 2 chandeliers uniques (ts1 et ts2)
        assert result.height == 2

        # ts1 doit avoir le prix du dump le plus récent (4502.0)
        ts1_row = result.filter(pl.col("window_start") == ts1)
        assert ts1_row["open"][0] == 4502.0

    def test_aggregate_multiple_tickers(self, tmp_settings):
        """Agréger des dumps de tickers différents les conserve tous."""
        ts = datetime(2025, 6, 1, 9, 30, 0, tzinfo=timezone.utc)

        df1 = _make_df("ESM5", [ts], [4500.0])
        save_raw_dump(df1, "ES", "ESM5", "20260711T183000", tmp_settings)

        df2 = _make_df("ESU5", [ts], [4600.0])
        save_raw_dump(df2, "ES", "ESU5", "20260711T183000", tmp_settings)

        result = aggregate("ES", tmp_settings)

        # 2 chandeliers (même timestamp mais tickers différents)
        assert result.height == 2
        tickers = result["ticker"].unique().sort().to_list()
        assert tickers == ["ESM5", "ESU5"]

    def test_aggregate_sorted_by_window_start(self, tmp_settings):
        """L'agrégé est trié par window_start."""
        ts1 = datetime(2025, 6, 1, 9, 31, 0, tzinfo=timezone.utc)
        ts2 = datetime(2025, 6, 1, 9, 30, 0, tzinfo=timezone.utc)

        # Dump avec timestamps dans le désordre
        df = _make_df("ESM5", [ts1, ts2], [4501.0, 4500.0])
        save_raw_dump(df, "ES", "ESM5", "20260711T183000", tmp_settings)

        result = aggregate("ES", tmp_settings)

        # Vérifier que c'est trié par window_start ascendant
        assert result["window_start"][0] < result["window_start"][1]

    def test_aggregate_cast_categorical(self, tmp_settings):
        """Les colonnes run_id, ticker, product_code sont castées en Categorical."""
        ts = datetime(2025, 6, 1, 9, 30, 0, tzinfo=timezone.utc)
        df = _make_df("ESM5", [ts], [4500.0])
        save_raw_dump(df, "ES", "ESM5", "20260711T183000", tmp_settings)

        result = aggregate("ES", tmp_settings)

        # Vérifier le dtype Categorical
        assert result.schema["ticker"] == pl.Categorical
        assert result.schema["run_id"] == pl.Categorical
        assert result.schema["product_code"] == pl.Categorical

    def test_aggregate_cast_int32(self, tmp_settings):
        """Les colonnes volume et transactions sont castées en Int32 (persisté dans le Parquet)."""
        ts = datetime(2025, 6, 1, 9, 30, 0, tzinfo=timezone.utc)
        df = _make_df("ESM5", [ts], [4500.0])
        save_raw_dump(df, "ES", "ESM5", "20260711T183000", tmp_settings)

        result = aggregate("ES", tmp_settings)

        # Vérifier le dtype Int32 (au lieu du Int64 de l'API)
        assert result.schema["volume"] == pl.Int32
        assert result.schema["transactions"] == pl.Int32

        # Vérifier que le cast est persisté dans le Parquet (relire le fichier)
        reread = read_aggregate("ES", tmp_settings)
        assert reread.schema["volume"] == pl.Int32
        assert reread.schema["transactions"] == pl.Int32

    def test_aggregate_empty(self, tmp_settings):
        """Agréger sans dump retourne un DataFrame vide."""
        result = aggregate("ES", tmp_settings)
        assert result.is_empty()

    def test_aggregate_writes_sidecar(self, tmp_settings):
        """L'agrégation écrit un sidecar .meta.json avec les bonnes métadonnées."""
        ts = datetime(2025, 6, 1, 9, 30, 0, tzinfo=timezone.utc)
        df = _make_df("ESM5", [ts], [4500.0])
        save_raw_dump(df, "ES", "ESM5", "20260711T183000", tmp_settings)

        aggregate("ES", tmp_settings)

        from massivibe.storage.parquet_io import read_meta

        meta = read_meta(tmp_settings.aggregate_path("ES"))
        assert meta is not None
        assert meta["product_code"] == "ES"
        assert meta["source_dump_count"] == 1
        assert "dedup_removed_count" in meta
