"""Fetch des contrats futures via l'endpoint /futures/v1/contracts.

Cet endpoint retourne la liste des contrats d'un produit (filtre ``product_code``)
avec leurs spécifications : ticker, first/last_trade_date, settlement_date,
trade_tick_size, etc. La pagination est gérée par :meth:`MassiveClient.get_paginated`.
"""

from __future__ import annotations

import polars as pl

from massivibe.api.client import MassiveClient
from massivibe.config import Settings
from massivibe.logging_setup import get_logger

logger = get_logger("contracts")

# Endpoint REST Massive pour les contrats futures
_CONTRACTS_PATH = "/futures/v1/contracts"


def fetch_contracts(
    client: MassiveClient,
    product_code: str,
    settings: Settings,
    active: bool | None = None,
    date: str | None = None,
) -> pl.DataFrame:
    """Récupère tous les contrats d'un produit via /futures/v1/contracts.

    La pagination (``next_url``) est gérée automatiquement par le client.
    On filtre par ``product_code`` et on utilise ``contracts_page_limit``
    (max API = 1000) pour minimiser le nombre de pages.

    :param client: Client Massive authentifié.
    :param product_code: Code produit (ex: "ES", "NQ").
    :param settings: Configuration (pour contracts_page_limit).
    :param active: Si True, ne retourner que les contrats actifs à la date ``date``.
    :param date: Date au format YYYY-MM-DD pour un lookup point-in-time.
    :return: DataFrame Polars avec les contrats, triés par ``first_trade_date``.
    """
    logger.info(f"Fetch /futures/v1/contracts?product_code={product_code}")

    results = client.get_paginated(
        _CONTRACTS_PATH,
        product_code=product_code,
        active=active,
        date=date,
        limit=settings.contracts_page_limit,
        sort="first_trade_date.asc",
    )

    if not results:
        logger.warning(f"Aucun contrat trouvé pour product_code={product_code}")
        return pl.DataFrame()

    df = pl.DataFrame(results)

    # Conversion des dates string (YYYY-MM-DD) en type Date de Polars
    for col in ("first_trade_date", "last_trade_date", "settlement_date", "date"):
        if col in df.columns:
            df = df.with_columns(pl.col(col).str.to_date("%Y-%m-%d").alias(col))

    # Tri par first_trade_date pour faciliter la construction de la RolloverChain
    if "first_trade_date" in df.columns:
        df = df.sort("first_trade_date")

    logger.info(f"Récupéré {df.height} contrat(s) pour {product_code}")
    return df
