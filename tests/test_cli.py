"""Tests du module cli.py."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from massivibe.cli import _render_df, main


class TestCliCommands:
    """Tests des commandes CLI."""

    def test_config_command(self, tmp_path, monkeypatch, capsys):
        """`massivibe config` affiche la configuration."""
        # Créer un .env et config.toml dans tmp_path
        env_file = tmp_path / ".env"
        env_file.write_text("MASSIVE_API_KEY=test_key_12345\n", encoding="utf-8")

        config_toml = tmp_path / "config.toml"
        config_toml.write_text(
            """
[instruments]
product_codes = ["ES"]

[fetch]
timeframe = "1min"

[storage]
data_dir = "./data"
log_dir = "./logs"

[logging]
level = "DEBUG"
""",
            encoding="utf-8",
        )

        monkeypatch.chdir(tmp_path)

        result = main(["config"])

        assert result == 0
        captured = capsys.readouterr()
        assert "Configuration MassiVibe" in captured.out
        assert "ES" in captured.out

    def test_config_command_no_key(self, tmp_path, monkeypatch, capsys):
        """`massivibe config` affiche NON CONFIGURÉE si pas de clé."""
        env_file = tmp_path / ".env"
        env_file.write_text("MASSIVE_API_KEY=\n", encoding="utf-8")

        config_toml = tmp_path / "config.toml"
        config_toml.write_text(
            """
[instruments]
product_codes = ["ES"]

[logging]
level = "DEBUG"
""",
            encoding="utf-8",
        )

        monkeypatch.chdir(tmp_path)

        result = main(["config"])

        assert result == 0
        captured = capsys.readouterr()
        assert "NON CONFIGURÉE" in captured.out

    def test_no_command_prints_help(self, capsys):
        """`massivibe` sans commande affiche l'aide."""
        result = main([])
        assert result == 0

    def test_status_command_empty(self, tmp_path, monkeypatch, capsys):
        """`massivibe status` sur un environnement vide ne crash pas."""
        env_file = tmp_path / ".env"
        env_file.write_text("MASSIVE_API_KEY=test_key\n", encoding="utf-8")

        config_toml = tmp_path / "config.toml"
        config_toml.write_text(
            """
[instruments]
product_codes = ["ES"]

[storage]
data_dir = "{}"

[logging]
level = "INFO"
""".format(tmp_path / "data"),
            encoding="utf-8",
        )

        monkeypatch.chdir(tmp_path)

        result = main(["status", "--product", "ES"])

        assert result == 0
        captured = capsys.readouterr()
        assert "absent" in captured.out

    def test_setup_key_creates_env(self, tmp_path, monkeypatch):
        """`massivibe setup-key` crée le fichier .env."""
        monkeypatch.chdir(tmp_path)

        # Simuler l'input de la clé
        import io

        monkeypatch.setattr("getpass.getpass", lambda prompt: "my_secret_key_123")

        result = main(["setup-key"])

        assert result == 0
        env_path = tmp_path / ".env"
        assert env_path.exists()
        content = env_path.read_text(encoding="utf-8")
        assert "MASSIVE_API_KEY=my_secret_key_123" in content
        assert "MASSIVE_BASE_URL=https://api.massive.com" in content

    def test_setup_key_empty_key_aborts(self, tmp_path, monkeypatch, capsys):
        """`massivibe setup-key` avec clé vide → abandon."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("getpass.getpass", lambda prompt: "")

        result = main(["setup-key"])

        assert result == 1


class TestRenderDf:
    """Tests du helper _render_df (tri + limites d'affichage)."""

    def test_render_df_sort_descending(self, tmp_settings, capsys):
        """_render_df trie par ordre décroissant sur la colonne demandée."""
        df = pl.DataFrame(
            {
                "rollover_date": [date(2024, 1, 1), date(2025, 6, 1), date(2024, 9, 1)],
                "ticker": ["A", "B", "C"],
            }
        )
        _render_df(df, tmp_settings, sort_col="rollover_date")
        out = capsys.readouterr().out
        # Le tri décroissant → B (2025-06-01) doit apparaître avant A (2024-01-01)
        idx_b = out.find("B")
        idx_a = out.find("A")
        assert idx_b < idx_a, f"B devrait apparaître avant A (tri desc). out={out!r}"

    def test_render_df_limit_rows(self, tmp_settings, capsys):
        """_render_df tronque à display_max_rows lignes."""
        # Créer plus de lignes que la limite (50 par défaut dans tmp_settings)
        small_settings = tmp_settings.model_copy(update={"display_max_rows": 3})
        df = pl.DataFrame({"ticker": [f"T{i}" for i in range(10)]})
        _render_df(df, small_settings)
        out = capsys.readouterr().out
        assert "limité à 3 lignes sur 10" in out

    def test_render_df_limit_columns(self, tmp_settings, capsys):
        """_render_df tronque à display_max_columns colonnes."""
        small_settings = tmp_settings.model_copy(update={"display_max_columns": 5})
        df = pl.DataFrame({f"col{i}": [1] for i in range(10)})
        _render_df(df, small_settings)
        out = capsys.readouterr().out
        assert "limité à 5 colonnes sur 10" in out

    def test_render_df_empty(self, tmp_settings, capsys):
        """_render_df sur un DataFrame vide affiche 'Aucune donnée'."""
        df = pl.DataFrame()
        _render_df(df, tmp_settings)
        out = capsys.readouterr().out
        assert "Aucune donnée" in out

    def test_render_df_missing_sort_col(self, tmp_settings, capsys):
        """_render_df avec sort_col absent du DataFrame → pas de tri, pas d'erreur."""
        df = pl.DataFrame({"ticker": ["A", "B"]})
        # sort_col "rollover_date" n'existe pas dans df → pas de tri, pas de crash
        _render_df(df, tmp_settings, sort_col="rollover_date")
        out = capsys.readouterr().out
        assert "A" in out and "B" in out
