"""Fetchers multi-type — dispatch par type d'instrument.

Chaque type d'instrument a sa propre logique de fetch (endpoint, schéma,
rollover ou non). Ce package fournit une fabrique :func:`get_fetcher` qui
retourne le fetcher adapté à un :class:`massivibe.instruments.Instrument`.

Fetchers disponibles :
- :class:`FuturesFetcher` (futures) — RolloverChain + ``/futures/v1/aggs``.
- :class:`StocksFetcher` (stocks) — ``/v2/aggs/ticker`` + corporate actions
  (splits, dividends) pour l'ajustement à la query.
- :class:`V2SingleSymbolFetcher` (forex, indices) — ``/v2/aggs/ticker`` sans
  corporate actions (préfixes ``C:`` / ``I:`` via ``Instrument.api_ticker``).
- :class:`OptionsFetcher` (options) — scaffold (``NotImplementedError``).
"""

from __future__ import annotations

from massivibe.instruments import Instrument, InstrumentType
from massivibe.pipeline.fetchers.base import InstrumentFetcher
from massivibe.pipeline.fetchers.futures import FuturesFetcher
from massivibe.pipeline.fetchers.options import OptionsFetcher
from massivibe.pipeline.fetchers.stocks import StocksFetcher
from massivibe.pipeline.fetchers.v2_single import V2SingleSymbolFetcher


def get_fetcher(instrument: Instrument) -> InstrumentFetcher:
    """Retourne le fetcher adapté au type d'instrument.

    :param instrument: Instrument cible.
    :return: Une instance de :class:`InstrumentFetcher`.
    :raises NotImplementedError: Pour options (scaffold).
    """
    t = instrument.type
    if t == InstrumentType.FUTURES:
        return FuturesFetcher()
    if t == InstrumentType.STOCKS:
        return StocksFetcher()
    if t in (InstrumentType.FOREX, InstrumentType.INDICES):
        return V2SingleSymbolFetcher()
    if t == InstrumentType.OPTIONS:
        return OptionsFetcher()
    raise NotImplementedError(
        f"Fetch pour le type '{t.value}' n'est pas encore implémenté. "
        f"Instrument: {instrument.key}"
    )
