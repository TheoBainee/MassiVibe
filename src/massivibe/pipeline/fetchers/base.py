"""Classe de base abstraite des fetchers d'instruments.

Un :class:`InstrumentFetcher` encapsule la logique de récupération et
d'historisation des chandeliers OHLCV pour un type d'instrument donné :

- Déterminer la plage à fetcher (premier run vs incrémental vs extension).
- Appeler l'endpoint API adapté (futures ``/v1`` ou v2 ``/v2``).
- Sauvegarder les dumps bruts (1 fichier Parquet par run).
- Déclencher l'agrégation après le fetch.

Le retour est un dict de résultat ``{status, candles, ...}`` homogène entre types,
exploitable par la commande ``massivibe fetch``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from massivibe.api.client import MassiveClient
from massivibe.config import Settings
from massivibe.instruments import Instrument


class InstrumentFetcher(ABC):
    """Interface abstraite des fetchers multi-type."""

    @abstractmethod
    def fetch(
        self,
        instrument: Instrument,
        settings: Settings,
        client: MassiveClient,
        force: bool = False,
        dry_run: bool = False,
    ) -> dict[str, object]:
        """Historise un instrument.

        :param instrument: Instrument à historiser.
        :param settings: Configuration.
        :param client: Client Massive authentifié.
        :param force: Si True, relance même si déjà fait aujourd'hui.
        :param dry_run: Si True, calcule le plan sans appeler l'API ni écrire.
        :return: Dict de résultat ``{status, candles, ...}`` (homogène entre types).
        """
        ...
