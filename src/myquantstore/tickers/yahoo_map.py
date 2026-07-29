"""Mapping symbole interne Massive → ticker Yahoo Finance.

Règles V1 (stocks) :
1. Override explicite ``settings.yahoo_ticker_overrides[symbol]``
2. Skip warrants/units (suffixes ``.WS``, ``.U``, ``.W``, ``.R``, ``.RT``…)
3. Remplacer ``.`` par ``-`` (ex: ``BRK.A`` → ``BRK-A``)
4. Sinon identité (``AAPL`` → ``AAPL``)
"""

from __future__ import annotations

import re

from myquantstore.instruments import Instrument, InstrumentType

# Suffixes Massive typiques des warrants / units / rights — hors scope V1.
_SKIP_SUFFIXES = (
    ".WS",
    ".W",
    ".U",
    ".UN",
    ".R",
    ".RT",
    ".RIGHTS",
    ".PW",
    ".P",  # preferred souvent différent sur Yahoo
)

# Suffixes déjà avec tiret (après éventuelle conversion)
_SKIP_SUFFIXES_DASH = tuple(s.replace(".", "-") for s in _SKIP_SUFFIXES)

_SKIP_RE = re.compile(
    r"(\.WS|\.W$|\.U$|\.UN$|\.R$|\.RT$|\.RIGHTS$|\.PW$|-WS$|-W$|-U$|-UN$|-R$|-RT$)$",
    re.IGNORECASE,
)


class UnmappableTickerError(ValueError):
    """Symbole non mappable vers Yahoo (skip V1 ou type non supporté)."""


def is_skipped_stock_symbol(symbol: str) -> bool:
    """True si le symbole stock est hors scope V1 (warrants/units…)."""
    s = symbol.strip().upper()
    if _SKIP_RE.search(s):
        return True
    # Preferred / class shares purement suffixés .P
    return bool(s.endswith(".P") or s.endswith("-P"))


def to_yahoo_ticker(
    instrument: Instrument,
    overrides: dict[str, str] | None = None,
) -> str:
    """Convertit un instrument interne vers un ticker Yahoo.

    :param instrument: Instrument (symbole nu Massive).
    :param overrides: Table ``{symbol: yahoo_ticker}`` (config ``[yahoo]``).
    :return: Ticker Yahoo (ex: ``BRK-A``, ``AAPL``).
    :raises UnmappableTickerError: Type non supporté ou symbole skippé V1.
    """
    overrides = overrides or {}
    symbol = instrument.symbol.strip()

    if symbol in overrides:
        return overrides[symbol]
    # Clé type:symbol aussi acceptée
    if instrument.key in overrides:
        return overrides[instrument.key]

    if instrument.type != InstrumentType.STOCKS:
        raise UnmappableTickerError(
            f"Mapping Yahoo V1 stocks only — reçu {instrument.key}"
        )

    if is_skipped_stock_symbol(symbol):
        raise UnmappableTickerError(
            f"Symbole '{symbol}' skippé en V1 (warrant/unit/preferred). "
            "Ajoutez un override dans [yahoo] ticker_overrides si besoin."
        )

    # Massive class shares : BRK.A → BRK-A
    return symbol.replace(".", "-")
