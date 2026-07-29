"""Tests du module pipeline/aggregator.py."""

from __future__ import annotations

from datetime import UTC, datetime

import polars as pl

from myquantstore.pipeline.aggregator import aggregate
from myquantstore.storage.aggregate_cache import aggregate_exists, read_aggregate
from myquantstore.storage.raw_dumps import save_raw_dump


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

    def test_aggregate_single_dump(self, tmp_settings, es_instrument):
        ts = datetime(2025, 6, 1, 9, 30, 0, tzinfo=UTC)
        df = _make_df("ESM5", [ts], [4500.0])

        save_raw_dump(df, es_instrument, "ESM5", "20260711T183000", tmp_settings)

        result = aggregate(es_instrument, tmp_settings)

        assert result.height == 1
        assert aggregate_exists(es_instrument, tmp_settings)

    def test_aggregate_dedup_overlapping_dumps(self, tmp_settings, es_instrument):
        """Deux dumps chevauchants → dédup sur (window_start, ticker) keep=last."""
        ts1 = datetime(2025, 6, 1, 9, 30, 0, tzinfo=UTC)
        ts2 = datetime(2025, 6, 1, 9, 31, 0, tzinfo=UTC)

        df1 = _make_df("ESM5", [ts1, ts2], [4500.0, 4501.0])
        save_raw_dump(df1, es_instrument, "ESM5", "20260704T180000", tmp_settings)

        df2 = _make_df("ESM5", [ts1], [4502.0])
        save_raw_dump(df2, es_instrument, "ESM5", "20260711T183000", tmp_settings)

        result = aggregate(es_instrument, tmp_settings)

        assert result.height == 2
        ts1_row = result.filter(pl.col("window_start") == ts1)
        assert ts1_row["open"][0] == 4502.0

    def test_aggregate_multiple_tickers(self, tmp_settings, es_instrument):
        """Agréger des dumps de tickers différents les conserve tous."""
        ts = datetime(2025, 6, 1, 9, 30, 0, tzinfo=UTC)

        df1 = _make_df("ESM5", [ts], [4500.0])
        save_raw_dump(df1, es_instrument, "ESM5", "20260711T183000", tmp_settings)

        df2 = _make_df("ESU5", [ts], [4600.0])
        save_raw_dump(df2, es_instrument, "ESU5", "20260711T183000", tmp_settings)

        result = aggregate(es_instrument, tmp_settings)

        assert result.height == 2
        tickers = result["ticker"].unique().sort().to_list()
        assert tickers == ["ESM5", "ESU5"]

    def test_aggregate_sorted_by_window_start(self, tmp_settings, es_instrument):
        ts1 = datetime(2025, 6, 1, 9, 31, 0, tzinfo=UTC)
        ts2 = datetime(2025, 6, 1, 9, 30, 0, tzinfo=UTC)

        df = _make_df("ESM5", [ts1, ts2], [4501.0, 4500.0])
        save_raw_dump(df, es_instrument, "ESM5", "20260711T183000", tmp_settings)

        result = aggregate(es_instrument, tmp_settings)

        assert result["window_start"][0] < result["window_start"][1]

    def test_aggregate_cast_categorical(self, tmp_settings, es_instrument):
        """Les colonnes run_id, ticker, symbol, instrument_type sont Categorical."""
        ts = datetime(2025, 6, 1, 9, 30, 0, tzinfo=UTC)
        df = _make_df("ESM5", [ts], [4500.0])
        save_raw_dump(df, es_instrument, "ESM5", "20260711T183000", tmp_settings)

        result = aggregate(es_instrument, tmp_settings)

        assert result.schema["ticker"] == pl.Categorical
        assert result.schema["run_id"] == pl.Categorical
        assert result.schema["symbol"] == pl.Categorical
        assert result.schema["instrument_type"] == pl.Categorical

    def test_aggregate_cast_int32(self, tmp_settings, es_instrument):
        """volume et transactions sont castées en Int32 (persisté dans le Parquet)."""
        ts = datetime(2025, 6, 1, 9, 30, 0, tzinfo=UTC)
        df = _make_df("ESM5", [ts], [4500.0])
        save_raw_dump(df, es_instrument, "ESM5", "20260711T183000", tmp_settings)

        result = aggregate(es_instrument, tmp_settings)

        assert result.schema["volume"] == pl.Int32
        assert result.schema["transactions"] == pl.Int32

        reread = read_aggregate(es_instrument, tmp_settings)
        assert reread.schema["volume"] == pl.Int32
        assert reread.schema["transactions"] == pl.Int32

    def test_aggregate_empty(self, tmp_settings, es_instrument):
        result = aggregate(es_instrument, tmp_settings)
        assert result.is_empty()

    def test_aggregate_writes_sidecar(self, tmp_settings, es_instrument):
        """L'agrégation écrit un sidecar .meta.json avec les bonnes métadonnées."""
        ts = datetime(2025, 6, 1, 9, 30, 0, tzinfo=UTC)
        df = _make_df("ESM5", [ts], [4500.0])
        save_raw_dump(df, es_instrument, "ESM5", "20260711T183000", tmp_settings)

        aggregate(es_instrument, tmp_settings)

        from myquantstore.storage.parquet_io import read_meta

        meta = read_meta(tmp_settings.aggregate_path(es_instrument))
        assert meta is not None
        assert meta["symbol"] == "ES"
        assert meta["instrument_type"] == "futures"
        assert meta["source_dump_count"] == 1
        assert "dedup_removed_count" in meta
