"""Fetch des corporate actions stocks via ``/stocks/v1/splits`` et ``/stocks/v1/dividends``.

Ces endpoints retournent l'historique des splits et dividends d'un stock, avec
pour chacun un ``historical_adjustment_factor`` cumulatif permettant d'ajuster
les prix historiques :

- **Splits** : pour un prix à la date D, trouver le premier split dont
  ``execution_date > D`` et multiplier le prix brut par son
  ``historical_adjustment_factor``.
- **Dividends** : même mécanisme avec ``ex_dividend_date``.

MassiVibe stocke les prix **bruts** (``adjusted=false`` au fetch) et applique
les ajustements à la query (toggle ``--no-split`` / ``--adjust``).

État d'implémentation :
- :func:`fetch_splits` : **implémenté** (nécessaire pour ``--no-split``).
  - :func:`fetch_dividends` : **implémenté** (nécessaire pour ``--adjust``).
"""

from __future__ import annotations

import polars as pl

from massivibe.api.client import MassiveClient
from massivibe.config import Settings
from massivibe.logging_setup import get_logger

logger = get_logger("corp_actions")

# Endpoint REST Massive pour les corporate actions stocks
_SPLITS_PATH = "/stocks/v1/splits"
_DIVIDENDS_PATH = "/stocks/v1/dividends"


def fetch_splits(
    client: MassiveClient,
    ticker: str,
    settings: Settings,
) -> pl.DataFrame:
    """Récupère l'historique des splits d'un stock.

    :param client: Client Massive authentifié.
    :param ticker: Symbole nu du stock (ex: "AAPL").
    :param settings: Configuration (pour splits_page_limit).
    :return: DataFrame Polars des splits, triés par ``execution_date``.
        Colonnes : ticker, execution_date (Date), adjustment_type,
        historical_adjustment_factor, split_from, split_to, id.
    """
    logger.info(f"Fetch /stocks/v1/splits?ticker={ticker}")

    results = client.get_paginated(
        _SPLITS_PATH,
        ticker=ticker,
        limit=settings.splits_page_limit,
        sort="execution_date.asc",
    )

    if not results:
        logger.warning(f"Aucun split trouvé pour {ticker}")
        return pl.DataFrame()

    df = pl.DataFrame(results)

    # Conversion execution_date (string YYYY-MM-DD) -> Date
    if "execution_date" in df.columns:
        df = df.with_columns(
            pl.col("execution_date").str.to_date("%Y-%m-%d").alias("execution_date")
        )

    if "execution_date" in df.columns:
        df = df.sort("execution_date")

    logger.info(f"Récupéré {df.height} split(s) pour {ticker}")
    return df


def fetch_dividends(
    client: MassiveClient,
    ticker: str,
    settings: Settings,
) -> pl.DataFrame:
    """Récupère l'historique des dividends d'un stock.

    Utilise le même mécanisme que les splits : historical_adjustment_factor
    pour ajuster les prix historiques (premier ex_dividend_date > D).

    :param client: Client Massive authentifié.
    :param ticker: Symbole nu du stock (ex: "AAPL").
    :param settings: Configuration (pour dividends_page_limit).
    :return: DataFrame Polars des dividends, triés par ``ex_dividend_date``.
        Colonnes incluent : ticker, ex_dividend_date (Date), historical_adjustment_factor,
        cash_amount, currency, declaration_date, pay_date, record_date, frequency, etc.
    """
    logger.info(f"Fetch /stocks/v1/dividends?ticker={ticker}")

    results = client.get_paginated(
        _DIVIDENDS_PATH,
        ticker=ticker,
        limit=settings.dividends_page_limit,
        sort="ex_dividend_date.asc",
    )

    if not results:
        logger.warning(f"Aucun dividend trouvé pour {ticker}")
        return pl.DataFrame()

    df = pl.DataFrame(results)

    # Conversion ex_dividend_date (string YYYY-MM-DD) -> Date
    if "ex_dividend_date" in df.columns:
        df = df.with_columns(
            pl.col("ex_dividend_date").str.to_date("%Y-%m-%d").alias("ex_dividend_date")
        )

    if "ex_dividend_date" in df.columns:
        df = df.sort("ex_dividend_date")

    logger.info(f"Récupéré {df.height} dividend(s) pour {ticker}")
    return df
