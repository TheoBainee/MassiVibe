"""Orchestration de l'historisation (commande ``fetch``).

:func:`run_fetch` orchestre l'historisation des chandeliers OHLCV pour une
liste d'instruments. Le dispatch par type d'instrument est délégué à la
fabrique :func:`myquantstore.pipeline.fetchers.get_fetcher` :

- ``futures`` → :class:`FuturesFetcher` (RolloverChain + ``/futures/v1/aggs``).
- ``stocks`` → :class:`StocksFetcher` (v2 ``adjusted=false`` + corporate actions).
- ``forex`` / ``indices`` → :class:`V2SingleSymbolFetcher` (v2, pas de corporate actions).
- ``options`` → :class:`OptionsFetcher` (scaffold ``NotImplementedError``).

Le retour est un dict de résultats par instrument ``{instrument_key: {status, candles, ...}}``,
homogène entre types.
"""

from __future__ import annotations

from typing import cast

from myquantstore.api.client import MassiveClient
from myquantstore.config import Settings
from myquantstore.instruments import Instrument
from myquantstore.logging_setup import get_logger
from myquantstore.pipeline.fetchers import get_fetcher

logger = get_logger("fetch")


def run_fetch(
    settings: Settings,
    client: MassiveClient,
    instruments: list[Instrument] | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, dict[str, object]]:
    """Lance l'historisation pour un ou plusieurs instruments.

    :param settings: Configuration.
    :param client: Client Massive authentifié.
    :param instruments: Liste des instruments à historiser. Si None, utilise
        ``settings.all_instruments()`` (tous les instruments configurés).
    :param force: Si True, relance même si déjà fait aujourd'hui.
    :param dry_run: Si True, calcule le plan sans appeler l'API ni écrire.
    :return: Dictionnaire des résultats par instrument ``{key: {status, ...}}``.
    """
    if instruments is None:
        instruments = settings.all_instruments()

    logger.info(f"Début de l'historisation pour {len(instruments)} instrument(s): {[str(i) for i in instruments]}")

    results: dict[str, dict[str, object]] = {}

    for instrument in instruments:
        try:
            fetcher = get_fetcher(instrument)
            result = fetcher.fetch(instrument, settings, client, force=force, dry_run=dry_run)
        except NotImplementedError as e:
            logger.warning(f"Instrument {instrument.key} non implémenté: {e}")
            result = {"status": "not_implemented", "instrument": str(instrument), "error": str(e)}
        except Exception as e:
            logger.error(f"Erreur lors du fetch de {instrument.key}: {e}")
            result = {"status": "error", "instrument": str(instrument), "error": str(e)}
        results[str(instrument)] = result

    total_candles = sum(cast("int", r.get("candles", 0)) for r in results.values())
    total_skipped = sum(1 for r in results.values() if r.get("status") == "skipped")
    logger.info(
        f"Historisation terminée: {total_candles} chandeliers récupérés, "
        f"{total_skipped} instrument(s) skippé(s)"
    )

    return results
