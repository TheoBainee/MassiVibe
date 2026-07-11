"""Tests du module config.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from massivibe.config import Settings, generate_run_ts, load_settings


class TestSettings:
    """Tests de la classe Settings et de load_settings."""

    def test_load_settings_from_config_toml(self, tmp_path: Path):
        """load_settings charge config.toml correctement."""
        # Créer un config.toml de test
        config_toml = tmp_path / "config.toml"
        config_toml.write_text(
            """
[instruments]
product_codes = ["NQ", "ES"]

[fetch]
timeframe = "1min"
overlap_buffer_days = 1
history_months = 24
requests_per_minute = 10
page_limit = 50000
contracts_page_limit = 1000
max_retries = 6

[storage]
data_dir = "./test_data"
log_dir = "./test_logs"

[contracts_cache]
ttl_days = 30

[rollover]
days_before_expiry = 7

[tests]
data_quality_trigger = 0.1

[logging]
level = "INFO"
""",
            encoding="utf-8",
        )

        # Créer un .env de test
        env_file = tmp_path / ".env"
        env_file.write_text("MASSIVE_API_KEY=test_key_12345\n", encoding="utf-8")

        # Changer le cwd pour que .env et config.toml soient trouvés
        import os

        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            settings = load_settings(config_path=config_toml)
        finally:
            os.chdir(old_cwd)

        assert settings.api_key == "test_key_12345"
        assert settings.product_codes == ["NQ", "ES"]
        assert settings.timeframe == "1min"
        assert settings.overlap_buffer_days == 1
        assert settings.history_months == 24
        assert settings.requests_per_minute == 10
        assert settings.contracts_page_limit == 1000
        assert settings.data_dir == "./test_data"
        assert settings.log_dir == "./test_logs"
        assert settings.contracts_ttl_days == 30
        assert settings.days_before_expiry == 7
        assert settings.data_quality_trigger == 0.1
        assert settings.log_level == "INFO"

    def test_settings_defaults(self):
        """Les valeurs par défaut de Settings sont correctes."""
        settings = Settings(api_key="test")
        assert settings.product_codes == ["NQ", "ES", "RTY", "YM"]
        assert settings.overlap_buffer_days == 1
        assert settings.history_months == 24
        assert settings.requests_per_minute == 10
        assert settings.contracts_page_limit == 1000
        assert settings.max_retries == 6
        assert settings.contracts_ttl_days == 30
        assert settings.days_before_expiry == 7
        assert settings.data_quality_trigger == 0.1
        assert settings.log_level == "DEBUG"

    def test_validation_product_codes_not_empty(self):
        """product_codes ne peut pas être vide."""
        with pytest.raises(Exception, match="product_codes"):
            Settings(api_key="test", product_codes=[])

    def test_validation_overlap_buffer_non_neg(self):
        """overlap_buffer_days doit être >= 0."""
        with pytest.raises(Exception, match="overlap_buffer_days"):
            Settings(api_key="test", overlap_buffer_days=-1)

    def test_validation_days_before_expiry_non_neg(self):
        """days_before_expiry doit être >= 0."""
        with pytest.raises(Exception, match="days_before_expiry"):
            Settings(api_key="test", days_before_expiry=-1)

    def test_validation_history_months_ge_1(self):
        """history_months doit être >= 1."""
        with pytest.raises(Exception, match="history_months"):
            Settings(api_key="test", history_months=0)

    def test_validation_requests_per_minute_non_neg(self):
        """requests_per_minute doit être >= 0."""
        with pytest.raises(Exception, match="requests_per_minute"):
            Settings(api_key="test", requests_per_minute=-1)

    def test_validation_max_retries_ge_1(self):
        """max_retries doit être >= 1."""
        with pytest.raises(Exception, match="max_retries"):
            Settings(api_key="test", max_retries=0)

    def test_validation_page_limit_range(self):
        """page_limit doit être entre 1 et 50000."""
        with pytest.raises(Exception, match="page_limit"):
            Settings(api_key="test", page_limit=0)
        with pytest.raises(Exception, match="page_limit"):
            Settings(api_key="test", page_limit=50001)

    def test_validation_contracts_page_limit_range(self):
        """contracts_page_limit doit être entre 1 et 1000."""
        with pytest.raises(Exception, match="contracts_page_limit"):
            Settings(api_key="test", contracts_page_limit=0)
        with pytest.raises(Exception, match="contracts_page_limit"):
            Settings(api_key="test", contracts_page_limit=1001)

    def test_validation_data_quality_trigger_positive(self):
        """data_quality_trigger doit être > 0."""
        with pytest.raises(Exception, match="data_quality_trigger"):
            Settings(api_key="test", data_quality_trigger=0)
        with pytest.raises(Exception, match="data_quality_trigger"):
            Settings(api_key="test", data_quality_trigger=-0.1)

    def test_helpers_chemins(self, tmp_settings):
        """Les helpers de chemins retournent les bons paths."""
        assert tmp_settings.raw_dumps_dir().name == "raw"
        assert tmp_settings.aggregate_dir().name == "aggregate"
        assert tmp_settings.aggregate_path("ES").name == "ES_continuous.parquet"
        assert tmp_settings.contracts_cache_path("ES").name == "ES.parquet"
        assert tmp_settings.contracts_meta_path("ES").name == "ES.meta.json"
        dump_path = tmp_settings.raw_dump_path("ES", "ESM5", "20260711T183000")
        assert "ES" in str(dump_path)
        assert "ESM5" in str(dump_path)
        assert "20260711T183000.parquet" in str(dump_path)


class TestGenerateRunTs:
    """Tests de generate_run_ts."""

    def test_format(self):
        """run_ts a le format YYYYMMDDTHHMMSS."""
        run_ts = generate_run_ts()
        assert len(run_ts) == 15
        assert run_ts[8] == "T"
        # Les 8 premiers caractères sont la date YYYYMMDD
        date_part = run_ts[:8]
        assert date_part.isdigit()
        # Les 6 derniers caractères sont l'heure HHMMSS
        time_part = run_ts[9:]
        assert time_part.isdigit()

    def test_uniqueness(self):
        """Deux appels successifs produisent des run_ts différents (à la seconde près)."""
        import time

        ts1 = generate_run_ts()
        time.sleep(1.1)
        ts2 = generate_run_ts()
        assert ts1 != ts2
