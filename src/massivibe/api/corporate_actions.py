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
- :func:`fetch_dividends` : **scaffold** (``NotImplementedError`` — ``--adjust``
  dividend n'est pas implémenté pour l'instant).
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

    Scaffold — lève :class:`NotImplementedError`. L'ajustement dividend
    (``--adjust`` pour stocks) n'est pas implémenté pour l'instant.
    """
    raise NotImplementedError(
        "fetch_dividends non implémenté — l'ajustement dividend (--adjust pour "
        "stocks) est planifié. Voir la spécification fonctionnelle."
    )
