"""Fetch des contrats futures via l'endpoint /futures/v1/contracts.

Cet endpoint retourne la liste des contrats d'un produit (filtre ``product_code``)
avec leurs spécifications : ticker, first/last_trade_date, settlement_date,
trade_tick_size, etc. La pagination est gérée par :meth:`MassiveClient.get_paginated`.

**Comportement point-in-time** : l'API renvoie une ligne par contrat actif à la date
spécifiée. Sans le paramètre ``date``, l'API renvoie TOUT l'historique des snapshots
quotidiens (une ligne par contrat ET par jour = volume énorme). On évite cela en
défendant ``date=today`` par défaut dans :func:`fetch_contracts`.

Pour récupérer les contrats expirés (nécessaires pour la RolloverChain sur 2 ans),
:func:`fetch_contracts_history` fait plusieurs appels avec des dates échelonnées
puis déduplique par ticker.

**Quirk API — combos taggés ``single``** : l'API ne distingue pas les combos
(spreads comme ``ESH6-ESM6``) des contrats singles dans le champ ``type`` — tous
sont taggés ``type="single"``. Le filtre API ``type=single`` n'exclut donc aucun
combo, et exclut en plus les contrats historiques antérieurs au 2025-03-12 (où
``type`` est ``null``). Seul critère fiable pour isoler les "vrais" contrats single :
la présence d'un ``-`` dans le ticker (indique un spread). Ce filtrage est fait
client-side dans :func:`fetch_contracts_history` et :class:`RolloverChain`.
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import timedelta

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
    """Récupère les contrats d'un produit pour une date donnée (snapshot point-in-time).

    Quand ``date=None``, utilise la date du jour → une seule ligne par contrat
    (évite le volume énorme des snapshots quotidiens).

    La pagination (``next_url``) est gérée automatiquement par le client.
    On filtre par ``product_code`` et on utilise ``contracts_page_limit``
    (max API = 1000) pour minimiser le nombre de pages.

    :param client: Client Massive authentifié.
    :param product_code: Code produit (ex: "ES", "NQ").
    :param settings: Configuration (pour contracts_page_limit).
    :param active: Si True, ne retourner que les contrats actifs à la date ``date``.
    :param date: Date au format YYYY-MM-DD pour un lookup point-in-time.
        Si None, utilise la date du jour.
    :return: DataFrame Polars avec les contrats, triés par ``first_trade_date``.
    """
    if date is None:
        date = date_type.today().isoformat()

    logger.info(f"Fetch /futures/v1/contracts?product_code={product_code}&date={date}")

    # NB: l'API n'accepte que sort ∈ {date, product_code, ticker} — pas first_trade_date.
    # On trie client-side par first_trade_date (voir plus bas).
    # On utilise sort=ticker.asc pour obtenir un ordre déterministe côté API.
    results = client.get_paginated(
        _CONTRACTS_PATH,
        product_code=product_code,
        active=active,
        date=date,
        limit=settings.contracts_page_limit,
        sort="ticker.asc",
    )

    if not results:
        logger.warning(f"Aucun contrat trouvé pour product_code={product_code} (date={date})")
        return pl.DataFrame()

    df = pl.DataFrame(results)

    # Conversion des dates string (YYYY-MM-DD) en type Date de Polars
    for col in ("first_trade_date", "last_trade_date", "settlement_date", "date"):
        if col in df.columns:
            df = df.with_columns(pl.col(col).str.to_date("%Y-%m-%d").alias(col))

    # Tri par first_trade_date pour faciliter la construction de la RolloverChain
    if "first_trade_date" in df.columns:
        df = df.sort("first_trade_date")

    logger.info(f"Récupéré {df.height} contrat(s) pour {product_code} (date={date})")
    return df


def fetch_contracts_history(
    client: MassiveClient,
    product_code: str,
    settings: Settings,
    active: bool | None = None,
) -> pl.DataFrame:
    """Récupère l'historique complet des contrats d'un produit (actifs + expirés).

    Fait plusieurs appels à :func:`fetch_contracts` avec des dates échelonnées
    sur ``history_months`` pour capturer les contrats expirés (nécessaires pour
    construire la RolloverChain sur la période d'historique ciblée).

    Étapes :
    1. Calculer les dates de snapshot (aujourd'hui + dates passées à intervalle régulier).
    2. Pour chaque date, appeler ``fetch_contracts`` (une page par date en général).
    3. Concaténer tous les résultats.
    4. Dédupliquer par ``ticker`` en gardant le snapshot le plus récent (pour
       ``trade_tick_size`` qui n'est disponible qu'à partir du 2025-03-12).
    5. Remplir les ``trade_tick_size`` manquants avec la valeur la plus commune
       du même produit (tous les contrats d'un produit ont le même tick size).
    6. Trier par ``first_trade_date``.

    :param client: Client Massive authentifié.
    :param product_code: Code produit (ex: "ES").
    :param settings: Configuration (pour history_months, snapshot_interval_months).
    :param active: Si True, ne retourner que les contrats actifs.
    :return: DataFrame Polars avec une ligne par contrat unique, triés par first_trade_date.
    """
    interval = settings.contracts_snapshot_interval_months

    # Si interval=0 ou pas d'historique : un seul snapshot à la date du jour
    if interval <= 0:
        logger.debug(f"Snapshot unique (interval=0) pour {product_code}")
        return fetch_contracts(client, product_code, settings, active=active)

    # Calculer les dates de snapshot : aujourd'hui + dates passées à intervalle régulier
    today = date_type.today()
    snapshot_dates = [today.isoformat()]
    months = settings.history_months
    for m in range(interval, months + 1, interval):
        # Approximation : 30 jours par mois
        d = today - timedelta(days=m * 30)
        snapshot_dates.append(d.isoformat())

    logger.info(
        f"Fetch historique contrats pour {product_code}: "
        f"{len(snapshot_dates)} snapshot(s) sur {months} mois (interval={interval}m)"
    )

    # Récupérer les contrats pour chaque date de snapshot
    all_dfs: list[pl.DataFrame] = []
    for d in snapshot_dates:
        df = fetch_contracts(client, product_code, settings, active=active, date=d)
        if not df.is_empty():
            all_dfs.append(df)

    if not all_dfs:
        logger.warning(f"Aucun contrat trouvé pour {product_code} sur tous les snapshots")
        return pl.DataFrame()

    # Concaténer tous les snapshots
    merged = pl.concat(all_dfs, how="diagonal_relaxed")

    # Déduplication par ticker : garder le snapshot le plus récent
    # (pour avoir trade_tick_size qui n'est disponible qu'à partir du 2025-03-12)
    if "date" in merged.columns and "ticker" in merged.columns:
        merged = merged.sort("date", descending=True).unique(subset=["ticker"], keep="first")

    # Remplir les trade_tick_size manquants avec la valeur la plus commune
    # parmi les contrats single uniquement (les combos ont un tick size différent).
    #
    # NOTE — Pourquoi on ne peut pas filtrer via l'API avec type=single :
    #   1. L'API tagge aussi les combos (spreads) avec type="single" — on a vérifié
    #      que /contracts?type=single&date=2025-06-20 renvoie les 24 combos d'ES.
    #   2. type=single exclut les contrats historiques antérieurs au 2025-03-12
    #      (type=null à cette époque) → on perdrait tout l'historique pré-2025.
    #   Seul critère fiable : le ticker. Les combos contiennent un "-"
    #   (ex: "ESH6-ESM6"). On filtre donc client-side.
    if "trade_tick_size" in merged.columns:
        singles_only = merged.filter(~pl.col("ticker").str.contains("-"))
        non_null = singles_only.filter(pl.col("trade_tick_size").is_not_null())
        if not non_null.is_empty():
            default_tick = non_null["trade_tick_size"].mode()[0]
            merged = merged.with_columns(
                pl.when(pl.col("trade_tick_size").is_null())
                .then(pl.lit(default_tick))
                .otherwise(pl.col("trade_tick_size"))
                .alias("trade_tick_size")
            )
            logger.debug(
                f"trade_tick_size manquants remplis avec {default_tick} pour {product_code}"
            )

    # Tri par first_trade_date
    if "first_trade_date" in merged.columns:
        merged = merged.sort("first_trade_date")

    logger.info(
        f"Récupéré {merged.height} contrat(s) unique(s) pour {product_code} "
        f"(après déduplication sur {len(snapshot_dates)} snapshots)"
    )
    return merged
