"""Cascade automatique des dépendances fonctionnelles.

Chaine de dépendances :

::

    contracts (cache /contracts) --> fetch (OHLCV) --> aggregate (fusion+dedup) --> query (lecture)

Chaque commande vérifie ses prérequis en cascade amont et déclenche
automatiquement les étapes manquantes avec des WARNING explicites.

Le flag ``--no-cascade`` désactive l'auto-cascade → erreur claire si prérequis
manquant (pour usage automatisé/cron où on veut un échec explicite).

Avant une cascade, on logge un snapshot ``status`` (état de chaque étape
pour chaque produit impliqué) pour visibilité.
"""

from __future__ import annotations

from massivibe.api.client import MassiveClient
from massivibe.config import Settings
from massivibe.contracts.cache import ContractsCache
from massivibe.contracts.rollover import RolloverChain
from massivibe.logging_setup import get_logger
from massivibe.pipeline.aggregator import aggregate
from massivibe.storage.aggregate_cache import aggregate_exists
from massivibe.storage.raw_dumps import raw_dumps_exist

logger = get_logger("cascade")


class CascadeError(Exception):
    """Levée quand un prérequis est manquant et que ``--no-cascade`` est actif."""

    def __init__(self, step: str, product_code: str, missing: str):
        self.step = step
        self.product_code = product_code
        self.missing = missing
        super().__init__(
            f"Prérequis manquant pour '{step}' sur {product_code}: {missing}. "
            f"Exécutez d'abord '{missing}' ou relancez sans --no-cascade."
        )


def print_status_snapshot(product_codes: list[str], settings: Settings) -> None:
    """Affiche un snapshot de l'état de chaque étape pour les produits impliqués.

    Logge l'état du cache contrats, des dumps bruts et du cache agrégé pour
    chaque produit — utilisé avant de dérouler une cascade.

    :param product_codes: Liste des produits à inspecter.
    :param settings: Configuration.
    """
    logger.info("[status] == Avant cascade ==")
    for pc in product_codes:
        # État du cache contrats
        cache = ContractsCache(pc, settings)
        if cache.exists:
            last_fetched = cache.get_last_fetched()
            contracts_status = f"frais({last_fetched})" if last_fetched else "présent"
        else:
            contracts_status = "absent"

        # État des dumps bruts
        dumps_status = "présent" if raw_dumps_exist(pc, settings) else "absent"

        # État du cache agrégé
        agg_status = "OK" if aggregate_exists(pc, settings) else "absent"

        logger.info(
            f"[status] {pc}: contracts={contracts_status}, dumps={dumps_status}, aggregate={agg_status}"
        )


def ensure_contracts(
    product_code: str,
    client: MassiveClient,
    settings: Settings,
    no_cascade: bool = False,
) -> None:
    """Vérifie le cache contrats. Si absent/périmé → WARNING + auto-refresh.

    :param product_code: Code produit.
    :param client: Client Massive authentifié.
    :param settings: Configuration.
    :param no_cascade: Si True, lève CascadeError au lieu de rafraîchir.
    :raises CascadeError: Si le cache est absent/périmé et ``no_cascade=True``.
    """
    cache = ContractsCache(product_code, settings)

    if cache.exists and cache._is_fresh():
        logger.debug(f"[cascade] Cache contrats frais pour {product_code} — OK")
        return

    # Cache absent ou périmé
    status = "absent" if not cache.exists else "périmé"

    if no_cascade:
        raise CascadeError("fetch", product_code, "contracts")

    logger.warning(
        f"[cascade] Cache contrats {status} pour {product_code} — rafraîchissement automatique…"
    )
    cache.get(client, force_refresh=True)


def ensure_raw_dumps(
    product_code: str,
    client: MassiveClient,
    settings: Settings,
    no_cascade: bool = False,
) -> None:
    """Vérifie l'existence de dumps bruts. Si aucun → WARNING + auto-fetch.

    :param product_code: Code produit.
    :param client: Client Massive authentifié.
    :param settings: Configuration.
    :param no_cascade: Si True, lève CascadeError au lieu de déclencher fetch.
    :raises CascadeError: Si aucun dump et ``no_cascade=True``.
    """
    if raw_dumps_exist(product_code, settings):
        logger.debug(f"[cascade] Dumps bruts présents pour {product_code} — OK")
        return

    # Pas de dumps
    if no_cascade:
        raise CascadeError("aggregate", product_code, "fetch")

    logger.warning(f"[cascade] Aucun dump trouvé pour {product_code} — lancement fetch…")

    # Cascade en amont : s'assurer que les contrats sont disponibles
    ensure_contracts(product_code, client, settings, no_cascade=False)

    # Lancer le fetch
    from massivibe.pipeline.historian import run_fetch
    run_fetch(settings, client, product_codes=[product_code], force=True)


def ensure_aggregate(
    product_code: str,
    client: MassiveClient,
    settings: Settings,
    no_cascade: bool = False,
) -> RolloverChain | None:
    """Vérifie l'existence du cache agrégé. Si absent → WARNING + auto-aggregate.

    :param product_code: Code produit.
    :param client: Client Massive authentifié.
    :param settings: Configuration.
    :param no_cascade: Si True, lève CascadeError au lieu de déclencher aggregate.
    :return: La RolloverChain du produit (nécessaire pour aggregate).
    :raises CascadeError: Si pas d'agrégé et ``no_cascade=True``.
    """
    if aggregate_exists(product_code, settings):
        logger.debug(f"[cascade] Agrégé présent pour {product_code} — OK")
        # Construire la chaîne pour la retourner (utile pour query)
        cache = ContractsCache(product_code, settings)
        # Passer le client si fourni (au cas où le cache serait absent/périmé)
        contracts_df = cache.get(client) if client is not None else cache.get()
        return RolloverChain(product_code, contracts_df, settings.days_before_expiry)

    # Pas d'agrégé
    if no_cascade:
        raise CascadeError("query", product_code, "aggregate")

    logger.warning(f"[cascade] Agrégé absent pour {product_code} — lancement aggregate…")

    # Cascade en amont : s'assurer que les dumps bruts existent
    ensure_raw_dumps(product_code, client, settings, no_cascade=False)

    # Récupérer la chaîne de rollover (nécessaire pour aggregate)
    cache = ContractsCache(product_code, settings)
    contracts_df = cache.get(client)
    chain = RolloverChain(product_code, contracts_df, settings.days_before_expiry)

    # Lancer l'agrégation
    aggregate(product_code, settings)

    return chain
