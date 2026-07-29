"""Tests du module storage/parquet_io.py."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from myquantstore.storage.parquet_io import read_meta, read_parquet, write_meta, write_parquet


class TestWriteReadParquet:
    """Tests de write_parquet / read_parquet avec sidecar .meta.json."""

    def test_write_creates_parquet_and_sidecar(self, tmp_path: Path):
        """write_parquet crée le .parquet ET le .meta.json."""
        df = pl.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
        path = tmp_path / "test.parquet"

        write_parquet(df, path)

        assert path.exists()
        meta_path = path.with_suffix(".meta.json")
        assert meta_path.exists()

    def test_read_roundtrip(self, tmp_path: Path):
        """write puis read retourne le même DataFrame."""
        df = pl.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
        path = tmp_path / "test.parquet"
        write_parquet(df, path)

        df_read = read_parquet(path)
        assert df_read.height == 3
        assert df_read.columns == ["a", "b"]

    def test_sidecar_contains_common_fields(self, tmp_path: Path):
        """Le sidecar contient les champs communs (schema_version, created_at, etc.)."""
        df = pl.DataFrame({"a": [1, 2, 3]})
        path = tmp_path / "test.parquet"
        write_parquet(df, path)

        meta = read_meta(path)
        assert meta is not None
        assert meta["schema_version"] == "1.0"
        assert "created_at" in meta
        assert meta["row_count"] == 3
        assert meta["columns"] == ["a"]
        assert "dtypes" in meta
        assert "file_size_bytes" in meta

    def test_sidecar_contains_extra_meta(self, tmp_path: Path):
        """Les extra_meta passés à write_parquet sont dans le sidecar."""
        df = pl.DataFrame({"a": [1]})
        path = tmp_path / "test.parquet"
        write_parquet(df, path, product_code="ES", ticker="ESM5", run_ts="20260711T183000")

        meta = read_meta(path)
        assert meta["product_code"] == "ES"
        assert meta["ticker"] == "ESM5"
        assert meta["run_ts"] == "20260711T183000"

    def test_read_meta_none_if_absent(self, tmp_path: Path):
        """read_meta retourne None si le sidecar n'existe pas."""
        path = tmp_path / "nonexistent.parquet"
        assert read_meta(path) is None

    def test_write_meta_updates_sidecar(self, tmp_path: Path):
        """write_meta met à jour le sidecar sans réécrire le Parquet."""
        df = pl.DataFrame({"a": [1]})
        path = tmp_path / "test.parquet"
        write_parquet(df, path, original="value")

        # Mettre à jour le sidecar
        meta = read_meta(path)
        meta["updated_field"] = "new_value"
        write_meta(path, meta)

        # Vérifier que le Parquet n'a pas changé
        df_read = read_parquet(path)
        assert df_read.height == 1

        # Vérifier que le sidecar a été mis à jour
        meta_updated = read_meta(path)
        assert meta_updated["updated_field"] == "new_value"
        assert meta_updated["original"] == "value"  # champ d'origine conservé

    def test_creates_parent_dirs(self, tmp_path: Path):
        """write_parquet crée les répertoires parents s'ils n'existent pas."""
        df = pl.DataFrame({"a": [1]})
        path = tmp_path / "deep" / "nested" / "dir" / "test.parquet"

        write_parquet(df, path)

        assert path.exists()
        assert path.with_suffix(".meta.json").exists()
