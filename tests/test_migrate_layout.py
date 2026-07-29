"""Tests de migration layout legacy → multi-résolution."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from myquantstore.instruments import Instrument, InstrumentType
from myquantstore.storage.migrate_layout import migrate_layout, needs_migration
from myquantstore.storage.parquet_io import write_parquet
from myquantstore.storage.raw_dumps import list_runs, read_all_runs, save_raw_dump


def _sample_df() -> pl.DataFrame:
    from datetime import datetime

    return pl.DataFrame(
        {
            "window_start": [datetime(2026, 1, 2, 14, 30)],
            "ticker": ["AAPL"],
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.5],
            "volume": [1000],
        }
    ).with_columns(pl.col("window_start").cast(pl.Datetime("ns")))


def test_migrate_aggregate_and_raw(tmp_settings):
    """Déplace aggregate legacy + dumps raw legacy vers …/1min/."""
    inst = Instrument(type=InstrumentType.STOCKS, symbol="AAPL")
    df = _sample_df()

    # Legacy aggregate : aggregate/stocks/AAPL.parquet
    legacy_agg = tmp_settings.legacy_aggregate_path(inst)
    write_parquet(df, legacy_agg, symbol="AAPL")

    # Legacy raw : raw/stocks/AAPL/AAPL/{run}.parquet (sans sous-dossier résolution)
    legacy_raw = (
        Path(tmp_settings.data_dir)
        / "raw"
        / "stocks"
        / "AAPL"
        / "AAPL"
        / "20260102T120000.parquet"
    )
    write_parquet(df, legacy_raw, symbol="AAPL", run_ts="20260102T120000")

    assert needs_migration(tmp_settings)
    report = migrate_layout(tmp_settings, resolution="1min", dry_run=False)
    assert report.aggregates_moved == 1
    assert report.raw_files_moved == 1
    assert not report.errors
    assert not needs_migration(tmp_settings)

    new_agg = tmp_settings.aggregate_path(inst, resolution="1min")
    assert new_agg.exists()
    assert not legacy_agg.exists()

    runs = list_runs(inst, "AAPL", tmp_settings, resolution="1min")
    assert runs == ["20260102T120000"]
    assert not legacy_raw.exists()

    # Idempotent
    report2 = migrate_layout(tmp_settings, resolution="1min", dry_run=False)
    assert report2.total_moved == 0


def test_migrate_dry_run_no_move(tmp_settings):
    inst = Instrument(type=InstrumentType.STOCKS, symbol="AAPL")
    legacy_agg = tmp_settings.legacy_aggregate_path(inst)
    write_parquet(_sample_df(), legacy_agg, symbol="AAPL")

    report = migrate_layout(tmp_settings, dry_run=True)
    assert report.aggregates_moved == 1
    assert legacy_agg.exists()
    assert not tmp_settings.aggregate_path(inst).exists()


def test_new_layout_save_and_read(tmp_settings, es_instrument, sample_aggs_df):
    """Le chemin multi-résolution fonctionne de bout en bout sans migration."""
    save_raw_dump(sample_aggs_df, es_instrument, "ESM5", "20260711T183000", tmp_settings)
    path = tmp_settings.raw_dump_path(es_instrument, "ESM5", "20260711T183000")
    assert "1min" in path.parts
    assert path.exists()
    df = read_all_runs(es_instrument, tmp_settings, resolution="1min")
    assert df.height == sample_aggs_df.height
