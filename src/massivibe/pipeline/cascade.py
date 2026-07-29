"""Cascade automatique des dépendances fonctionnelles (multi-type).

La chaîne de dépendances diffère selon le type d'instrument :

::

    futures : contracts (/futures/v1/contracts) --> fetch --> aggregate --> query
    stocks  : corporate_actions (splits + dividends) --> fetch --> aggregate --> query
    forex/indices :                             fetch --> aggregate --> query
    options : NotImplemented

Chaque commande vérifie ses prérequis en cascade amont et déclenche
automatiquement les étapes manquantes avec des WARNING explicites.

Le flag ``--no-cascade`` désactive l'auto-cascade → erreur claire si prérequis
manquant (pour usage automatisé/cron où on veut un échec explicite).

Avant une cascade, on logge un snapshot ``status`` (état de chaque étape pour
chaque instrument impliqué) pour visibilité.
"""

from __future__ import annotations

from massivibe.api.client import MassiveClient
from massivibe.chains import InstrumentChain, build_chain
from massivibe.config import Settings
from massivibe.contracts.cache import ContractsCache
from massivibe.corporate_actions.cache import CorporateActionsCache
from massivibe.instruments import Instrument, InstrumentType
from massivibe.logging_setup import get_logger
from massivibe.pipeline.aggregator import aggregate
from massivibe.storage.aggregate_cache import aggregate_exists
from massivibe.storage.raw_dumps import raw_dumps_exist

logger = get_logger("cascade")


class CascadeError(Exception):
    """Levée quand un prérequis est manquant et que ``--no-cascade`` est actif."""

    def __init__(self, step: str, instrument: Instrument, missing: str):
        self.step = step
        self.instrument = instrument
        self.missing = missing
        super().__init__(
            f"Prérequis manquant pour '{step}' sur {instrument.key}: {missing}. "
            f"Exécutez d'abord '{missing}' ou relancez sans --no-cascade."
        )


def print_status_snapshot(instruments: list[Instrument], settings: Settings) -> None:
    """Affiche un snapshot de l'état de chaque étape pour les instruments impliqués.

    Logge l'état du cache de listing (contrats futures / splits stocks), des
    dumps bruts et du cache agrégé pour chaque instrument.
    """
    logger.info("[status] == Avant cascade ==")
    for inst in instruments:
        # Cache de listing (type-dépendant)
        listing_status = _listing_status(inst, settings)
        dumps_status = "présent" if raw_dumps_exist(inst, settings) else "absent"
        agg_status = "OK" if aggregate_exists(inst, settings) else "absent"
        logger.info(
            f"[status] {inst.key}: listing={listing_status}, dumps={dumps_status}, aggregate={agg_status}"
        )


def _listing_status(instrument: Instrument, settings: Settings) -> str:
    """État du cache de listing (contrats futures / splits stocks)."""
    if instrument.type == InstrumentType.FUTURES:
        cache = ContractsCache(instrument.symbol, settings)
        if cache.exists:
            last_fetched = cache.get_last_fetched()
            return f"frais({last_fetched})" if last_fetched else "présent"
        return "absent"
    if instrument.type == InstrumentType.STOCKS:
        sc_cache = CorporateActionsCache(instrument.symbol, "splits", settings)
        dc_cache = CorporateActionsCache(instrument.symbol, "dividends", settings)
        parts = []
        if sc_cache.exists:
            lf = sc_cache.get_last_fetched()
            parts.append(f"splits frais({lf})" if lf else "splits présent")
        else:
            parts.append("splits absent")
        if dc_cache.exists:
            lf = dc_cache.get_last_fetched()
            parts.append(f"dividends frais({lf})" if lf else "dividends présent")
        else:
            parts.append("dividends absent")
        return " | ".join(parts)
    return "n/a"


def ensure_pre_fetch(
    instrument: Instrument,
    client: MassiveClient,
    settings: Settings,
    no_cascade: bool = False,
) -> None:
    """Vérifie le cache de listing adapté au type. Si absent/périmé → auto-refresh.

    - futures : cache contrats (``/futures/v1/contracts``).
    - stocks : cache splits + dividends (``/stocks/v1/splits``, ``/stocks/v1/dividends``).
    - forex/indices : no-op (pas de cache de listing nécessaire).
    - options : ``NotImplementedError``.
    """
    if instrument.type == InstrumentType.FUTURES:
        _ensure_contracts(instrument, client, settings, no_cascade)
    elif instrument.type == InstrumentType.STOCKS:
        _ensure_splits(instrument, client, settings, no_cascade)
        _ensure_dividends(instrument, client, settings, no_cascade)
    # forex/indices : pas de cache de listing en v1


def _ensure_contracts(
    instrument: Instrument,
    client: MassiveClient,
    settings: Settings,
    no_cascade: bool,
) -> None:
    """Vérifie le cache contrats futures. Si absent/périmé → auto-refresh."""
    cache = ContractsCache(instrument.symbol, settings)

    if cache.exists and cache._is_fresh():
        logger.debug(f"[cascade] Cache contrats frais pour {instrument.key} — OK")
        return

    status = "absent" if not cache.exists else "périmé"
    if no_cascade:
        raise CascadeError("fetch", instrument, "contracts")

    logger.warning(
        f"[cascade] Cache contrats {status} pour {instrument.key} — rafraîchissement automatique…"
    )
    cache.get(client, force_refresh=True)


def _ensure_splits(
    instrument: Instrument,
    client: MassiveClient,
    settings: Settings,
    no_cascade: bool,
) -> None:
    """Vérifie le cache splits stocks. Si absent/périmé → auto-refresh."""
    cache = CorporateActionsCache(instrument.symbol, "splits", settings)

    if cache.exists and cache._is_fresh():
        logger.debug(f"[cascade] Cache splits frais pour {instrument.key} — OK")
        return

    status = "absent" if not cache.exists else "périmé"
    if no_cascade:
        raise CascadeError("fetch", instrument, "splits")

    logger.warning(
        f"[cascade] Cache splits {status} pour {instrument.key} — rafraîchissement automatique…"
    )
    cache.get(client, force_refresh=True)


def _ensure_dividends(
    instrument: Instrument,
    client: MassiveClient,
    settings: Settings,
    no_cascade: bool,
) -> None:
    """Vérifie le cache dividends stocks. Si absent/périmé → auto-refresh."""
    cache = CorporateActionsCache(instrument.symbol, "dividends", settings)

    if cache.exists and cache._is_fresh():
        logger.debug(f"[cascade] Cache dividends frais pour {instrument.key} — OK")
        return

    status = "absent" if not cache.exists else "périmé"
    if no_cascade:
        raise CascadeError("fetch", instrument, "dividends")

    logger.warning(
        f"[cascade] Cache dividends {status} pour {instrument.key} — rafraîchissement automatique…"
    )
    cache.get(client, force_refresh=True)


def ensure_raw_dumps(
    instrument: Instrument,
    client: MassiveClient,
    settings: Settings,
    no_cascade: bool = False,
) -> None:
    """Vérifie l'existence de dumps bruts. Si aucun → WARNING + auto-fetch.

    Déclenche d'abord la cascade amont (cache de listing) adaptée au type.
    """
    if raw_dumps_exist(instrument, settings):
        logger.debug(f"[cascade] Dumps bruts présents pour {instrument.key} — OK")
        return

    if no_cascade:
        raise CascadeError("aggregate", instrument, "fetch")

    logger.warning(f"[cascade] Aucun dump trouvé pour {instrument.key} — lancement fetch…")

    # Cascade amont : cache de listing adapté au type
    ensure_pre_fetch(instrument, client, settings, no_cascade=False)

    # Lancer le fetch
    from massivibe.pipeline.historian import run_fetch

    run_fetch(settings, client, instruments=[instrument], force=True)


def ensure_aggregate(
    instrument: Instrument,
    client: MassiveClient,
    settings: Settings,
    no_cascade: bool = False,
) -> InstrumentChain | None:
    """Vérifie l'existence du cache agrégé. Si absent → WARNING + auto-aggregate.

    :return: La chaîne d'instrument (RolloverChain / SingleSymbolChain) pour la query.
    """
    if aggregate_exists(instrument, settings):
        logger.debug(f"[cascade] Agrégé présent pour {instrument.key} — OK")
        return _build_chain_for(instrument, client, settings)

    if no_cascade:
        raise CascadeError("query", instrument, "aggregate")

    logger.warning(f"[cascade] Agrégé absent pour {instrument.key} — lancement aggregate…")

    # Cascade amont : dumps bruts
    ensure_raw_dumps(instrument, client, settings, no_cascade=False)

    # Lancer l'agrégation
    aggregate(instrument, settings)

    return _build_chain_for(instrument, client, settings)


def _build_chain_for(instrument: Instrument, client: MassiveClient, settings: Settings) -> InstrumentChain:
    """Construit la chaîne d'instrument adaptée au type (avec cache de listing si requis)."""
    if instrument.type == InstrumentType.FUTURES:
        cache = ContractsCache(instrument.symbol, settings)
        contracts_df = cache.get(client) if client is not None else cache.get()
        return build_chain(instrument, contracts_df=contracts_df, days_before_expiry=settings.days_before_expiry)
    if instrument.type == InstrumentType.OPTIONS:
        return build_chain(instrument)
    # forex, stocks, indices → SingleSymbolChain (pas besoin de cache de listing)
    return build_chain(instrument)
