"""Modélisation multi-type des instruments financiers.

Massive.com expose 5 types d'instruments, chacun avec un endpoint et un schéma
de réponse différents :

================  ================================  ====================  ============
Type              Endpoint OHLCV                    Timestamp             Contrats
================  ================================  ====================  ============
``futures``       ``/futures/v1/aggs/{ticker}``    nanosecondes          OUI (rollover)
``forex``         ``/v2/aggs/ticker/{t}/range/...`` millisecondes         NON
``stocks``        ``/v2/aggs/ticker/{t}/range/...`` millisecondes         NON (splits)
``indices``       ``/v2/aggs/ticker/{t}/range/...`` millisecondes         NON (pas de volume)
``options``       ``/v2/aggs/ticker/{t}/range/...`` millisecondes         OUI (strike/call/put)
================  ================================  ====================  ============

Ce module fournit :

- :class:`InstrumentType` : enum des 5 types supportés.
- :class:`Instrument` : identifiant immutable ``(type, symbol)`` d'un instrument.
  Le ``symbol`` est **nu** (ex: ``"ES"``, ``"AAPL"``, ``"EURUSD"``, ``"NDX"``) ;
  le préfixe API éventuel (``C:``, ``I:``, ``O:``) est ajouté automatiquement par
  la propriété :attr:`Instrument.api_ticker`.
- :func:`parse_timeframe` : convertit un timeframe générique (``"1min"``) vers
  les paramètres attendus par l'endpoint v2 (``multiplier``, ``timespan``).

Les instruments futures sont des **produits** (ex: ``ES``) dont les contrats
individuels (ex: ``ESM5``) sont découverts via ``/futures/v1/contracts``. Pour
les 4 autres types, l'instrument == le ticker (un seul symbole, pas d'expiration).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class InstrumentType(StrEnum):
    """Les 5 types d'instruments supportés par l'API Massive.

    Hérite de ``StrEnum`` (Python 3.11+) pour être sérialisable en TOML/JSON
    directement — ``InstrumentType.FUTURES == "futures"`` est vrai.
    """

    FUTURES = "futures"
    FOREX = "forex"
    STOCKS = "stocks"
    INDICES = "indices"
    OPTIONS = "options"

    @property
    def has_contracts(self) -> bool:
        """True si le type a une notion de contrats expirants (rollover).

        Seuls ``futures`` et ``options`` ont des contrats individuels avec
        expiration. Les autres types sont des symboles uniques.
        """
        return self in (InstrumentType.FUTURES, InstrumentType.OPTIONS)

    @property
    def implemented(self) -> bool:
        """True si le type est fonctionnellement implémenté dans MyQuantStore.

        ``options`` est scaffoldé (``NotImplementedError``).
        """
        return self in (
            InstrumentType.FUTURES,
            InstrumentType.STOCKS,
            InstrumentType.FOREX,
            InstrumentType.INDICES,
        )



# Préfixe de ticker attendu par l'endpoint v2 (/v2/aggs/ticker/{ticker}/...).
# futures n'utilise pas l'endpoint v2 (endpoint dédié /futures/v1/aggs/{ticker}).
_TICKER_PREFIX: dict[InstrumentType, str] = {
    InstrumentType.FUTURES: "",
    InstrumentType.FOREX: "C:",
    InstrumentType.STOCKS: "",
    InstrumentType.INDICES: "I:",
    InstrumentType.OPTIONS: "O:",
}


@dataclass(frozen=True)
class Instrument:
    """Identifiant immutable d'un instrument financier.

    :ivar type: Type d'instrument (futures, forex, stocks, indices, options).
    :ivar symbol: Symbole **nu** sans préfixe API (ex: ``"ES"``, ``"AAPL"``,
        ``"EURUSD"``). Pour futures, c'est le code produit (ex: ``"ES"``) ;
        les contrats individuels (``ESM5``) sont découverts via ``/contracts``.
    """

    type: InstrumentType
    symbol: str

    @property
    def key(self) -> str:
        """Clé de stockage unique ``"{type}:{symbol}"`` (anti-collision).

        Ex: ``"futures:ES"``, ``"stocks:AAPL"``. Utilisé pour indexer les
        chaînes (chart) et distinguer deux symboles homonymes entre types.
        """
        return f"{self.type.value}:{self.symbol}"

    @property
    def api_ticker(self) -> str:
        """Ticker préfixé attendu par l'endpoint v2.

        Ajoute le préfixe du type (``C:`` forex, ``I:`` indices, ``O:`` options).
        Pour futures/stocks, retourne le symbole nu (pas de préfixe).

        NB : pour futures, ce n'est pas le ticker utilisé pour fetcher les
        chandeliers (on utilise le ticker du contrat individuel via la
        RolloverChain) — c'est uniquement le code produit.
        """
        prefix = _TICKER_PREFIX[self.type]
        return f"{prefix}{self.symbol}" if prefix else self.symbol

    @property
    def path_segment(self) -> str:
        """Segment de chemin pour le stockage = valeur de l'enum (ex: ``"futures"``)."""
        return self.type.value

    def __str__(self) -> str:
        return self.key


def parse_timeframe(timeframe: str) -> tuple[int, str]:
    """Convertit un timeframe générique en ``(multiplier, timespan)`` pour l'API v2.

    L'API v2 (``/v2/aggs/ticker/{t}/range/{multiplier}/{timespan}/...``) attend
    deux paramètres séparés, alors que l'endpoint futures utilise un seul
    paramètre ``resolution`` (ex: ``"1min"``). Cette fonction normalise le
    timeframe générique de la config (ex: ``"1min"``) vers le format v2.

    :param timeframe: Timeframe générique (ex: ``"1min"``, ``"5min"``,
        ``"1hour"``, ``"2hour"``).
    :return: Tuple ``(multiplier, timespan)`` (ex: ``(1, "minute")``).
    :raises ValueError: Si le format n'est pas reconnu.

    >>> parse_timeframe("1min")
    (1, 'minute')
    >>> parse_timeframe("5min")
    (5, 'minute')
    >>> parse_timeframe("1hour")
    (1, 'hour')
    """
    timeframe = timeframe.strip().lower()
    for unit, timespan in (("min", "minute"), ("hour", "hour"), ("day", "day")):
        if timeframe.endswith(unit):
            prefix = timeframe[: -len(unit)]
            multiplier = int(prefix) if prefix else 1
            return multiplier, timespan
    raise ValueError(
        f"Timeframe '{timeframe}' non reconnu. "
        "Formats supportés : '1min', '5min', '1hour', '2hour', ..."
    )
