"""Fetch du référentiel tickers Massive.

Endpoints :
- ``GET /v3/reference/tickers`` — univers multi-market (stocks, fx, indices, …)
- ``GET /v3/reference/tickers/types`` — codes de type (CS, ETF, …)
"""

from __future__ import annotations

from typing import Any

import polars as pl

from massivibe.api.client import MassiveClient
from massivibe.config import Settings
from massivibe.logging_setup import get_logger

logger = get_logger("tickers.api")

_TICKERS_PATH = "/v3/reference/tickers"
_TICKER_TYPES_PATH = "/v3/reference/tickers/types"

# Colonnes stables attendues (présence optionnelle selon market)
_TICKER_COLS = [
    "ticker",
    "name",
    "market",
    "locale",
    "type",
    "active",
    "primary_exchange",
    "currency_name",
    "currency_symbol",
    "base_currency_name",
    "base_currency_symbol",
    "cik",
    "composite_figi",
    "share_class_figi",
    "last_updated_utc",
    "delisted_utc",
]


def fetch_all_tickers(
    client: MassiveClient,
    settings: Settings,
    *,
    market: str | None = "stocks",
    active: bool | None = True,
    ticker_type: str | None = None,
    search: str | None = None,
    exchange: str | None = None,
) -> pl.DataFrame:
    """Récupère l'univers de tickers (paginé).

    :param client: Client Massive authentifié.
    :param settings: Config (``tickers_page_limit``).
    :param market: Filtre market API (``stocks``, ``fx``, ``indices``, …).
        ``None`` = tous les markets.
    :param active: ``True`` actifs, ``False`` delistés, ``None`` tous.
    :param ticker_type: Code type (ex: ``CS``, ``ETF``) — param API ``type``.
    :param search: Terme de recherche API (ticker/name).
    :param exchange: MIC primary exchange.
    :return: DataFrame Polars normalisé.
    """
    # API v3 : sort = nom de champ seul ; order = asc|desc (pas "ticker.asc")
    params: dict[str, Any] = {
        "limit": settings.tickers_page_limit,
        "sort": "ticker",
        "order": "asc",
    }
    if market is not None:
        params["market"] = market
    if active is not None:
        params["active"] = str(active).lower()  # API attend "true"/"false"
    if ticker_type is not None:
        params["type"] = ticker_type
    if search is not None:
        params["search"] = search
    if exchange is not None:
        params["exchange"] = exchange

    logger.info(
        f"Fetch {_TICKERS_PATH} market={market} active={active} type={ticker_type}"
    )
    results = client.get_paginated(_TICKERS_PATH, **params)

    if not results:
        logger.warning("Aucun ticker retourné par l'API")
        return _empty_tickers_frame()

    df = pl.DataFrame(results)
    df = _normalize_tickers_df(df)
    logger.info(f"Récupéré {df.height} ticker(s)")
    return df


def fetch_ticker_types(
    client: MassiveClient,
    *,
    asset_class: str | None = None,
    locale: str | None = None,
) -> pl.DataFrame:
    """Récupère les types de tickers supportés.

    :param client: Client Massive authentifié.
    :param asset_class: Filtre optionnel (stocks, options, crypto, fx, indices).
    :param locale: Filtre optionnel (us, global).
    :return: DataFrame ``code, description, asset_class, locale``.
    """
    params: dict[str, Any] = {}
    if asset_class is not None:
        params["asset_class"] = asset_class
    if locale is not None:
        params["locale"] = locale

    logger.info(f"Fetch {_TICKER_TYPES_PATH}")
    data = client.get(_TICKER_TYPES_PATH, **params)
    results = data.get("results") or []
    if not results:
        logger.warning("Aucun ticker type retourné")
        return pl.DataFrame(
            schema={
                "code": pl.Utf8,
                "description": pl.Utf8,
                "asset_class": pl.Utf8,
                "locale": pl.Utf8,
            }
        )

    df = pl.DataFrame(results)
    for col in ("code", "description", "asset_class", "locale"):
        if col not in df.columns:
            df = df.with_columns(pl.lit(None).cast(pl.Utf8).alias(col))
    df = df.select([c for c in ("code", "description", "asset_class", "locale") if c in df.columns])
    logger.info(f"Récupéré {df.height} ticker type(s)")
    return df


def _normalize_tickers_df(df: pl.DataFrame) -> pl.DataFrame:
    """Assure un schéma stable et un tri par ticker."""
    for col in _TICKER_COLS:
        if col not in df.columns:
            if col == "active":
                df = df.with_columns(pl.lit(None).cast(pl.Boolean).alias(col))
            else:
                df = df.with_columns(pl.lit(None).cast(pl.Utf8).alias(col))

    # Cast active en bool si string
    if df.schema.get("active") == pl.Utf8:
        df = df.with_columns(
            pl.col("active").str.to_lowercase().is_in(["true", "1", "yes"]).alias("active")
        )

    keep = [c for c in _TICKER_COLS if c in df.columns]
    df = df.select(keep)
    if "ticker" in df.columns:
        df = df.sort("ticker")
    return df


def _empty_tickers_frame() -> pl.DataFrame:
    schema: dict[str, pl.DataType] = {
        "ticker": pl.Utf8,
        "name": pl.Utf8,
        "market": pl.Utf8,
        "locale": pl.Utf8,
        "type": pl.Utf8,
        "active": pl.Boolean,
        "primary_exchange": pl.Utf8,
        "currency_name": pl.Utf8,
        "currency_symbol": pl.Utf8,
        "base_currency_name": pl.Utf8,
        "base_currency_symbol": pl.Utf8,
        "cik": pl.Utf8,
        "composite_figi": pl.Utf8,
        "share_class_figi": pl.Utf8,
        "last_updated_utc": pl.Utf8,
        "delisted_utc": pl.Utf8,
    }
    return pl.DataFrame(schema=schema)
