"""Tests du module tickers/search.py (search_tickers + add_to_config)."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from massivibe.instruments import InstrumentType
from massivibe.tickers.search import (
    add_to_config,
    build_type_to_asset_class_map,
    resolve_instrument_type,
    search_tickers,
)


class TestSearchTickers:
    """Tests du filtrage local search_tickers."""

    def test_filter_by_market(self, sample_all_tickers_df):
        """Filtre par marché (correspondance exacte insensible à la casse)."""
        df = sample_all_tickers_df
        # Tous les tickers du fixture sont 'stocks'
        r = search_tickers(df, market="stocks")
        assert r.height == 5

        r_empty = search_tickers(df, market="crypto")
        assert r_empty.is_empty()

    def test_filter_by_type(self, sample_all_tickers_df):
        """Filtre par code de type (sous-chaîne insensible à la casse)."""
        df = sample_all_tickers_df
        r = search_tickers(df, type_code="ETF")
        assert r.height == 1
        assert r["ticker"].to_list() == ["SPY"]

    def test_filter_by_name_contains(self, sample_all_tickers_df):
        """Filtre par sous-chaîne dans le nom (insensible à la casse)."""
        df = sample_all_tickers_df
        r = search_tickers(df, name_contains="apple")
        assert r.height == 1
        assert r["ticker"].to_list() == ["AAPL"]

        r2 = search_tickers(df, name_contains="corp")
        assert set(r2["ticker"].to_list()) == {"MSFT", "NVDA"}

    def test_filter_by_exchange(self, sample_all_tickers_df):
        """Filtre par sous-chaîne dans primary_exchange."""
        df = sample_all_tickers_df
        r = search_tickers(df, exchange="nasdaq")
        assert r.height == 4  # AAPL, MSFT, TSLA, NVDA (pas SPY qui est ARCA)

    def test_filter_ticker_substring(self, sample_all_tickers_df):
        """Filtre ticker par sous-chaîne."""
        df = sample_all_tickers_df
        r = search_tickers(df, ticker="MS")
        assert r["ticker"].to_list() == ["MSFT"]

    def test_filter_ticker_exact_list(self, sample_all_tickers_df):
        """Liste de tickers exacts séparés par virgules."""
        df = sample_all_tickers_df
        r = search_tickers(df, ticker="AAPL,TSLA,NVDA")
        assert set(r["ticker"].to_list()) == {"AAPL", "TSLA", "NVDA"}

    def test_filter_active_true(self, sample_all_tickers_df):
        """Filtre active=True ne renvoie que les actifs."""
        df = sample_all_tickers_df.with_columns(
            pl.when(pl.col("ticker") == "TSLA")
            .then(pl.lit(False))
            .otherwise(pl.col("active"))
            .alias("active")
        )
        r = search_tickers(df, active=True)
        assert "TSLA" not in r["ticker"].to_list()
        assert r.height == 4

    def test_filter_active_false(self, sample_all_tickers_df):
        """Filtre active=False renvoie les délistés."""
        df = sample_all_tickers_df.with_columns(
            pl.when(pl.col("ticker") == "TSLA")
            .then(pl.lit(False))
            .otherwise(pl.col("active"))
            .alias("active")
        )
        r = search_tickers(df, active=False)
        assert r["ticker"].to_list() == ["TSLA"]

    def test_limit(self, sample_all_tickers_df):
        """limit limite le nombre de résultats (après tri)."""
        df = sample_all_tickers_df
        r = search_tickers(df, market="stocks", limit=2)
        assert r.height == 2
        # Trié par ticker → AAPL, MSFT en premier
        assert r["ticker"].to_list() == ["AAPL", "MSFT"]

    def test_combined_filters(self, sample_all_tickers_df):
        """Combinaison de filtres (ET)."""
        df = sample_all_tickers_df
        r = search_tickers(df, market="stocks", exchange="nasdaq", type_code="CS")
        # CS + NASDAQ = AAPL, MSFT, TSLA, NVDA (pas SPY qui est ETF + ARCA)
        assert set(r["ticker"].to_list()) == {"AAPL", "MSFT", "TSLA", "NVDA"}

    def test_empty_df_returns_empty(self):
        """Un DataFrame vide renvoie un DataFrame vide."""
        empty = pl.DataFrame(
            {"ticker": [], "name": [], "market": [], "type": [], "active": []}
        )
        r = search_tickers(empty, market="stocks")
        assert r.is_empty()

    def test_sorted_by_ticker(self, sample_all_tickers_df):
        """Le résultat est trié par ticker ascendant."""
        df = sample_all_tickers_df
        # Mélanger l'ordre d'entrée
        shuffled = df.sort("name")
        r = search_tickers(shuffled, market="stocks")
        assert r["ticker"].to_list() == sorted(r["ticker"].to_list())


class TestResolveInstrumentType:
    """Tests du mapping type_code -> InstrumentType."""

    def test_cs_maps_to_stocks(self, sample_ticker_types_df):
        """Le code CS (Common Stock) mappe vers InstrumentType.STOCKS."""
        tmap = build_type_to_asset_class_map(sample_ticker_types_df)
        assert resolve_instrument_type("CS", tmap) == InstrumentType.STOCKS

    def test_etf_maps_to_stocks(self, sample_ticker_types_df):
        """Le code ETF mappe vers InstrumentType.STOCKS."""
        tmap = build_type_to_asset_class_map(sample_ticker_types_df)
        assert resolve_instrument_type("ETF", tmap) == InstrumentType.STOCKS

    def test_crypto_returns_none(self, sample_ticker_types_df):
        """Le code crypto n'est pas géré (None)."""
        tmap = build_type_to_asset_class_map(sample_ticker_types_df)
        assert resolve_instrument_type("crypto", tmap) is None

    def test_fx_returns_none_not_implemented(self, sample_ticker_types_df):
        """Le code CURRENCY (fx) n'est pas implémenté (None)."""
        tmap = build_type_to_asset_class_map(sample_ticker_types_df)
        assert resolve_instrument_type("CURRENCY", tmap) is None

    def test_unknown_code_returns_none(self, sample_ticker_types_df):
        """Un code inconnu retourne None."""
        tmap = build_type_to_asset_class_map(sample_ticker_types_df)
        assert resolve_instrument_type("UNKNOWN", tmap) is None

    def test_none_code_returns_none(self, sample_ticker_types_df):
        """Un code None retourne None."""
        tmap = build_type_to_asset_class_map(sample_ticker_types_df)
        assert resolve_instrument_type(None, tmap) is None


class TestAddToConfig:
    """Tests de --add-to-config (écriture dans config.toml)."""

    def _write_config(self, path: Path, stocks: list[str] | None = None) -> None:
        """Écrit un config.toml de test avec une section [instruments]."""
        stocks = stocks if stocks is not None else []
        path.parent.mkdir(parents=True, exist_ok=True)
        stocks_inline = ", ".join(f'"{s}"' for s in stocks)
        path.write_text(
            "[instruments]\n"
            'futures = ["ES"]\n'
            "forex = []\n"
            f"stocks = [{stocks_inline}]\n"
            "indices = []\n"
            "options = []\n"
            "\n"
            "[futures]\n"
            "days_before_expiry = 7\n",
            encoding="utf-8",
        )

    def test_add_new_tickers(self, tmp_path, sample_all_tickers_df, sample_ticker_types_df, tmp_settings):
        """Ajoute les tickers non présents à la section stocks."""
        cfg = tmp_path / "config.toml"
        self._write_config(cfg, stocks=["AAPL"])  # AAPL déjà présent

        # Recherche tous les stocks
        results = search_tickers(sample_all_tickers_df, market="stocks")
        summary = add_to_config(results, sample_ticker_types_df, tmp_settings, config_path=cfg)

        assert summary["added"] == {"stocks": ["MSFT", "NVDA", "SPY", "TSLA"]}
        assert "AAPL" in summary["skipped_duplicates"]
        assert summary["backup_path"] is not None

        # Vérifier le contenu du config.toml
        import tomllib

        with open(cfg, "rb") as f:
            data = tomllib.load(f)
        assert set(data["instruments"]["stocks"]) == {"AAPL", "MSFT", "NVDA", "SPY", "TSLA"}
        # La section [futures] est préservée
        assert data["futures"]["days_before_expiry"] == 7

    def test_dedup_skips_existing(self, tmp_path, sample_all_tickers_df, sample_ticker_types_df, tmp_settings):
        """Les tickers déjà dans la config sont ignorés (doublons)."""
        cfg = tmp_path / "config.toml"
        self._write_config(cfg, stocks=["AAPL", "MSFT", "NVDA"])

        results = search_tickers(sample_all_tickers_df, market="stocks")
        summary = add_to_config(results, sample_ticker_types_df, tmp_settings, config_path=cfg)

        # Seuls SPY et TSLA sont nouveaux
        assert summary["added"] == {"stocks": ["SPY", "TSLA"]}
        assert set(summary["skipped_duplicates"]) == {"AAPL", "MSFT", "NVDA"}

    def test_backup_created(self, tmp_path, sample_all_tickers_df, sample_ticker_types_df, tmp_settings):
        """Un backup config.toml.bak est créé avant l'écriture."""
        cfg = tmp_path / "config.toml"
        self._write_config(cfg, stocks=["AAPL"])
        original = cfg.read_text(encoding="utf-8")

        results = search_tickers(sample_all_tickers_df, market="stocks")
        add_to_config(results, sample_ticker_types_df, tmp_settings, config_path=cfg)

        backup = cfg.with_suffix(".toml.bak")
        assert backup.exists()
        assert backup.read_text(encoding="utf-8") == original

    def test_dry_run_does_not_write(self, tmp_path, sample_all_tickers_df, sample_ticker_types_df, tmp_settings):
        """dry_run=True n'écrit pas le fichier."""
        cfg = tmp_path / "config.toml"
        self._write_config(cfg, stocks=[])
        original = cfg.read_text(encoding="utf-8")

        results = search_tickers(sample_all_tickers_df, market="stocks")
        summary = add_to_config(
            results, sample_ticker_types_df, tmp_settings, config_path=cfg, dry_run=True
        )

        # Le récapitulatif est quand même calculé
        assert summary["added"]
        # Mais le fichier n'a pas changé
        assert cfg.read_text(encoding="utf-8") == original
        # Et pas de backup
        assert not cfg.with_suffix(".toml.bak").exists()

    def test_missing_columns_raises(self, tmp_path, sample_ticker_types_df, tmp_settings):
        """search_df sans colonnes ticker/type lève une erreur."""
        cfg = tmp_path / "config.toml"
        bad_df = pl.DataFrame({"foo": [1, 2]})
        with pytest.raises(ValueError, match="colonnes 'ticker' et 'type'"):
            add_to_config(bad_df, sample_ticker_types_df, tmp_settings, config_path=cfg)

    def test_creates_config_if_absent(self, tmp_path, sample_all_tickers_df, sample_ticker_types_df, tmp_settings):
        """Si le config.toml n'existe pas, il est créé avec la section [instruments]."""
        cfg = tmp_path / "sub" / "config.toml"  # sous-répertoire inexistant
        results = search_tickers(sample_all_tickers_df, market="stocks")
        add_to_config(results, sample_ticker_types_df, tmp_settings, config_path=cfg)

        assert cfg.exists()
        import tomllib

        with open(cfg, "rb") as f:
            data = tomllib.load(f)
        assert set(data["instruments"]["stocks"]) == {"AAPL", "MSFT", "NVDA", "SPY", "TSLA"}
