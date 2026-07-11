"""Tests du module cli.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from massivibe.cli import main


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
