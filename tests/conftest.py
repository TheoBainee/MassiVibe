"""Fixtures partagées pour les tests MassiVibe.

Fournit :
- ``tmp_settings`` : Settings avec data_dir/cache_dir/log_dir dans un tmp_path.
- ``es_instrument`` / ``nq_instrument`` : instruments futures de test.
- ``sample_contracts_df`` : DataFrame Polars de contrats ES simulés (avec trade_tick_size).
- ``sample_aggs_df`` : DataFrame Polars de chandeliers OHLCV simulés.
- ``sample_chain`` : RolloverChain construite à partir de sample_contracts_df.
- ``respx_mock`` : fixture fournie par respx pour mocker httpx.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
import pytest

from massivibe.config import Settings
from massivibe.contracts.rollover import RolloverChain
from massivibe.instruments import Instrument, InstrumentType


@pytest.fixture
def tmp_settings(tmp_path: Path) -> Settings:
    """Settings avec tous les chemins dans tmp_path (isolé du système de fichiers réel)."""
    return Settings(
        api_key="test_key_12345",
        base_url="https://api.test.massive.com",
        futures=["ES", "NQ"],
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
        cache_dir=str(tmp_path / "data" / "cache"),
        contracts_cache_subdir="contracts",
        corporate_actions_cache_subdir="corporate_actions",
        log_dir=str(tmp_path / "logs"),
        instrument_cache_ttl_days=30,
        contracts_snapshot_interval_months=0,  # un seul snapshot pour les tests (rapide)
        days_before_expiry=7,
        splits_page_limit=5000,
        dividends_page_limit=5000,
        data_quality_trigger=0.1,
        log_level="DEBUG",
        display_max_rows=50,
        display_max_columns=20,
    )


@pytest.fixture
def es_instrument() -> Instrument:
    """Instrument futures ES (E-mini S&P 500)."""
    return Instrument(type=InstrumentType.FUTURES, symbol="ES")


@pytest.fixture
def nq_instrument() -> Instrument:
    """Instrument futures NQ (E-mini Nasdaq)."""
    return Instrument(type=InstrumentType.FUTURES, symbol="NQ")


@pytest.fixture
def aapl_instrument() -> Instrument:
    """Instrument stocks AAPL."""
    return Instrument(type=InstrumentType.STOCKS, symbol="AAPL")


@pytest.fixture
def sample_contracts_df() -> pl.DataFrame:
    """DataFrame de contrats ES simulés (3 contrats trimestriels, avec trade_tick_size)."""
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
                datetime(2025, 6, 1, 9, 30, 0, tzinfo=UTC),
                datetime(2025, 6, 1, 9, 31, 0, tzinfo=UTC),
                datetime(2025, 6, 1, 9, 32, 0, tzinfo=UTC),
                datetime(2025, 6, 1, 9, 33, 0, tzinfo=UTC),
                datetime(2025, 6, 1, 9, 34, 0, tzinfo=UTC),
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
    """DataFrame avec quelques prix non conformes au tick size (<1%)."""
    df = sample_aggs_df.clone()
    df = df.with_columns(
        pl.when(pl.col("window_start") == datetime(2025, 6, 1, 9, 31, 0, tzinfo=UTC))
        .then(pl.lit(4501.27))
        .otherwise(pl.col("open"))
        .alias("open")
    )
    return df


@pytest.fixture
def sample_aggs_df_corrupted(sample_aggs_df: pl.DataFrame) -> pl.DataFrame:
    """DataFrame avec beaucoup de prix non conformes au tick size (>=5%)."""
    df = sample_aggs_df.clone()
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


# --- Fixtures pour les tests tickers (reference /v3) ---


@pytest.fixture
def all_tickers_api_response() -> dict:
    """Réponse JSON simulée de /v3/reference/tickers (page 1, market=stocks)."""
    return {
        "count": 3,
        "status": "OK",
        "request_id": "test_req_tickers_001",
        "results": [
            {
                "ticker": "AAPL",
                "name": "Apple Inc",
                "market": "stocks",
                "locale": "us",
                "type": "CS",
                "active": True,
                "currency_name": "usd",
                "primary_exchange": "NASDAQ",
                "composite_figi": "BBG000B9XRY4",
                "share_class_figi": "BBG001S5N8V8",
                "last_updated_utc": "2024-01-15",
                "delisted_utc": None,
                "cik": "0000320193",
            },
            {
                "ticker": "MSFT",
                "name": "Microsoft Corp",
                "market": "stocks",
                "locale": "us",
                "type": "CS",
                "active": True,
                "currency_name": "usd",
                "primary_exchange": "NASDAQ",
                "composite_figi": "BBG000B9XRY4",
                "share_class_figi": None,
                "last_updated_utc": "2024-02-20",
                "delisted_utc": None,
                "cik": "0000789019",
            },
            {
                "ticker": "SPY",
                "name": "SPDR S&P 500 ETF Trust",
                "market": "stocks",
                "locale": "us",
                "type": "ETF",
                "active": True,
                "currency_name": "usd",
                "primary_exchange": "ARCA",
                "composite_figi": None,
                "share_class_figi": None,
                "last_updated_utc": "2024-03-01",
                "delisted_utc": None,
                "cik": None,
            },
        ],
        "next_url": None,
    }


@pytest.fixture
def ticker_types_api_response() -> dict:
    """Réponse JSON simulée de /v3/reference/tickers/types."""
    return {
        "count": 4,
        "status": "OK",
        "request_id": "test_req_types_001",
        "results": [
            {"asset_class": "stocks", "code": "CS", "description": "Common Stock", "locale": "us"},
            {"asset_class": "stocks", "code": "ETF", "description": "Exchange Traded Fund", "locale": "us"},
            {"asset_class": "fx", "code": "CURRENCY", "description": "Currency pair", "locale": "global"},
            {"asset_class": "crypto", "code": "crypto", "description": "Cryptocurrency", "locale": "global"},
        ],
        "next_url": None,
    }


@pytest.fixture
def sample_all_tickers_df() -> pl.DataFrame:
    """DataFrame simulé de all-tickers (5 tickers multi-types, marché stocks)."""
    return pl.DataFrame(
        {
            "ticker": ["AAPL", "MSFT", "SPY", "TSLA", "NVDA"],
            "name": [
                "Apple Inc",
                "Microsoft Corp",
                "SPDR S&P 500 ETF Trust",
                "Tesla Inc",
                "NVIDIA Corp",
            ],
            "market": ["stocks", "stocks", "stocks", "stocks", "stocks"],
            "type": ["CS", "CS", "ETF", "CS", "CS"],
            "active": [True, True, True, True, True],
            "primary_exchange": ["NASDAQ", "NASDAQ", "ARCA", "NASDAQ", "NASDAQ"],
            "last_updated_utc": [date(2024, 1, 15), date(2024, 2, 20), date(2024, 3, 1), date(2024, 4, 1), date(2024, 5, 1)],
        }
    )


@pytest.fixture
def sample_ticker_types_df() -> pl.DataFrame:
    """DataFrame simulé du cache ticker-types (mapping code -> asset_class)."""
    return pl.DataFrame(
        {
            "code": ["CS", "ETF", "CURRENCY", "crypto"],
            "asset_class": ["stocks", "stocks", "fx", "crypto"],
            "description": ["Common Stock", "Exchange Traded Fund", "Currency pair", "Cryptocurrency"],
            "locale": ["us", "us", "global", "global"],
        }
    )
