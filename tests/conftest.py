"""Fixtures partagées pour les tests MassiVibe.

Fournit :
- ``tmp_settings`` : Settings avec data_dir/log_dir dans un tmp_path.
- ``sample_contracts_df`` : DataFrame Polars de contrats ES simulés (avec trade_tick_size).
- ``sample_aggs_df`` : DataFrame Polars de chandeliers OHLCV simulés.
- ``sample_chain`` : RolloverChain construite à partir de sample_contracts_df.
- ``respx_mock`` : fixture fournie par respx pour mocker httpx.
- ``clean_data_env`` : environnement de données propre (dossiers créés).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import polars as pl
import pytest

from massivibe.config import Settings
from massivibe.contracts.rollover import RolloverChain


@pytest.fixture
def tmp_settings(tmp_path: Path) -> Settings:
    """Settings avec tous les chemins dans tmp_path (isolé du système de fichiers réel)."""
    return Settings(
        api_key="test_key_12345",
        base_url="https://api.test.massive.com",
        product_codes=["ES", "NQ"],
        timeframe="1min",
        overlap_buffer_days=1,
        history_months=24,
        requests_per_minute=0,  # pas de throttle pour les tests
        page_limit=50000,
        contracts_page_limit=1000,
        max_retries=3,
        data_dir=str(tmp_path / "data"),
        raw_dumps_subdir="raw",
        aggregate_subdir="aggregate",
        contracts_cache_dir=str(tmp_path / "data" / "cache" / "contracts"),
        log_dir=str(tmp_path / "logs"),
        contracts_ttl_days=30,
        days_before_expiry=7,
        data_quality_trigger=0.1,
        log_level="DEBUG",
    )


@pytest.fixture
def sample_contracts_df() -> pl.DataFrame:
    """DataFrame de contrats ES simulés (3 contrats trimestriels).

    Inclut ``trade_tick_size`` pour tester la normalisation.
    """
    return pl.DataFrame(
        {
            "ticker": ["ESH5", "ESM5", "ESU5"],
            "first_trade_date": [
                date(2024, 12, 16),
                date(2025, 3, 17),
                date(2025, 6, 16),
            ],
            "last_trade_date": [
                date(2025, 3, 14),
                date(2025, 6, 13),
                date(2025, 9, 12),
            ],
            "settlement_date": [
                date(2025, 3, 14),
                date(2025, 6, 13),
                date(2025, 9, 12),
            ],
            "trade_tick_size": [0.25, 0.25, 0.25],
            "name": [
                "E-mini S&P 500 Mar 2025",
                "E-mini S&P 500 Jun 2025",
                "E-mini S&P 500 Sep 2025",
            ],
            "type": ["single", "single", "single"],
            "product_code": ["ES", "ES", "ES"],
            "active": [False, True, True],
        }
    )


@pytest.fixture
def sample_chain(sample_contracts_df: pl.DataFrame) -> RolloverChain:
    """RolloverChain construite à partir de sample_contracts_df."""
    return RolloverChain("ES", sample_contracts_df, days_before_expiry=7)


@pytest.fixture
def sample_aggs_df() -> pl.DataFrame:
    """DataFrame de chandeliers OHLCV 1min simulés pour ESM5.

    Les prix sont des multiples exacts de 0.25 (tick size de ES).
    """
    return pl.DataFrame(
        {
            "window_start": [
                datetime(2025, 6, 1, 9, 30, 0, tzinfo=timezone.utc),
                datetime(2025, 6, 1, 9, 31, 0, tzinfo=timezone.utc),
                datetime(2025, 6, 1, 9, 32, 0, tzinfo=timezone.utc),
                datetime(2025, 6, 1, 9, 33, 0, tzinfo=timezone.utc),
                datetime(2025, 6, 1, 9, 34, 0, tzinfo=timezone.utc),
            ],
            "ticker": ["ESM5"] * 5,
            "open": [4500.00, 4501.25, 4502.50, 4501.75, 4500.50],
            "high": [4501.00, 4502.50, 4503.00, 4502.25, 4501.00],
            "low": [4499.75, 4500.75, 4501.50, 4500.50, 4499.25],
            "close": [4500.75, 4501.50, 4502.25, 4501.00, 4500.25],
            "settlement_price": [4500.75, 4501.50, 4502.25, 4501.00, 4500.25],
            "volume": [100, 150, 200, 120, 80],
            "dollar_volume": [450075.0, 675225.0, 900450.0, 540120.0, 360020.0],
            "transactions": [50, 75, 100, 60, 40],
            "session_end_date": [date(2025, 6, 1)] * 5,
        }
    )


@pytest.fixture
def sample_aggs_df_noisy(sample_aggs_df: pl.DataFrame) -> pl.DataFrame:
    """DataFrame avec quelques prix non conformes au tick size (<1%).

    Utile pour tester --check-ticksize-accuracy avec statut ATTENTION.
    """
    df = sample_aggs_df.clone()
    # Modifier 1 valeur sur 25 (5 lignes x 5 colonnes = 25 valeurs) = 4% non conforme
    # On modifie la 2ème ligne, colonne open : 4501.25 -> 4501.27 (non multiple de 0.25)
    df = df.with_columns(
        pl.when(pl.col("window_start") == datetime(2025, 6, 1, 9, 31, 0, tzinfo=timezone.utc))
        .then(pl.lit(4501.27))
        .otherwise(pl.col("open"))
        .alias("open")
    )
    return df


@pytest.fixture
def sample_aggs_df_corrupted(sample_aggs_df: pl.DataFrame) -> pl.DataFrame:
    """DataFrame avec beaucoup de prix non conformes au tick size (>=5%).

    Utile pour tester --check-ticksize-accuracy avec statut ERREUR.
    """
    df = sample_aggs_df.clone()
    # Rendre toutes les valeurs de open non conformes (ajouter 0.1)
    df = df.with_columns((pl.col("open") + 0.1).alias("open"))
    df = df.with_columns((pl.col("high") + 0.1).alias("high"))
    df = df.with_columns((pl.col("low") + 0.1).alias("low"))
    df = df.with_columns((pl.col("close") + 0.1).alias("close"))
    return df


@pytest.fixture
def contracts_api_response() -> dict:
    """Réponse JSON simulée de /futures/v1/contracts (page 1)."""
    return {
        "request_id": "test_req_001",
        "status": "OK",
        "results": [
            {
                "ticker": "ESH5",
                "first_trade_date": "2024-12-16",
                "last_trade_date": "2025-03-14",
                "settlement_date": "2025-03-14",
                "trade_tick_size": 0.25,
                "name": "E-mini S&P 500 Mar 2025",
                "type": "single",
                "product_code": "ES",
                "active": True,
            },
            {
                "ticker": "ESM5",
                "first_trade_date": "2025-03-17",
                "last_trade_date": "2025-06-13",
                "settlement_date": "2025-06-13",
                "trade_tick_size": 0.25,
                "name": "E-mini S&P 500 Jun 2025",
                "type": "single",
                "product_code": "ES",
                "active": True,
            },
        ],
        "next_url": None,
    }


@pytest.fixture
def aggs_api_response() -> dict:
    """Réponse JSON simulée de /futures/v1/aggs/{ticker} (page 1)."""
    return {
        "request_id": "test_req_agg_001",
        "status": "OK",
        "results": [
            {
                "window_start": 1748770200000000000,  # 2025-06-01T09:30:00Z en ns
                "ticker": "ESM5",
                "open": 4500.00,
                "high": 4501.00,
                "low": 4499.75,
                "close": 4500.75,
                "settlement_price": 4500.75,
                "volume": 100,
                "dollar_volume": 450075.0,
                "transactions": 50,
                "session_end_date": "2025-06-01",
            },
            {
                "window_start": 1748770260000000000,  # 2025-06-01T09:31:00Z en ns
                "ticker": "ESM5",
                "open": 4501.25,
                "high": 4502.50,
                "low": 4500.75,
                "close": 4501.50,
                "settlement_price": 4501.50,
                "volume": 150,
                "dollar_volume": 675225.0,
                "transactions": 75,
                "session_end_date": "2025-06-01",
            },
        ],
        "next_url": None,
    }
