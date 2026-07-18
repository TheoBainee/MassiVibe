"""Recherche locale dans le cache tickers (Polars)."""

from __future__ import annotations

import re

import polars as pl

from massivibe.instruments import InstrumentType

# Préfixes API Massive sur les tickers (forex/indices/options)
_API_PREFIX_RE = re.compile(r"^[CIO]:", re.IGNORECASE)

# market API → InstrumentType MassiVibe (None = non supporté)
_MARKET_TO_TYPE: dict[str, InstrumentType | None] = {
    "stocks": InstrumentType.STOCKS,
    "otc": InstrumentType.STOCKS,
    "fx": InstrumentType.FOREX,
    "indices": InstrumentType.INDICES,
    "crypto": None,
    "options": InstrumentType.OPTIONS,
}


def strip_api_prefix(ticker: str) -> str:
    """Retire le préfixe ``C:`` / ``I:`` / ``O:`` d'un ticker API."""
    return _API_PREFIX_RE.sub("", ticker.strip())


def market_to_instrument_type(market: str | None) -> InstrumentType | None:
    """Mappe un ``market`` API vers :class:`InstrumentType` (ou None si non supporté)."""
    if market is None:
        return None
    return _MARKET_TO_TYPE.get(market.lower())


def search_tickers(
    df: pl.DataFrame,
    *,
    query: str | None = None,
    ticker: str | None = None,
    market: str | None = None,
    markets: list[str] | None = None,
    ticker_type: str | None = None,
    exchange: str | None = None,
    active: bool | None = None,
    limit: int | None = None,
) -> pl.DataFrame:
    """Filtre le DataFrame tickers selon les critères fournis.

    ``query`` : sous-chaîne case-insensitive dans ``ticker`` **ou** ``name``.
    ``ticker`` : égalité exacte (après strip préfixe), case-insensitive.
    ``market`` / ``markets`` : filtre(s) market (``markets`` prioritaire si fourni).
    ``limit`` : plafond data (souvent aligné sur l'affichage CLI).
    """
    if df.is_empty():
        return df

    out = df

    if ticker is not None:
        t = strip_api_prefix(ticker).upper()
        out = out.filter(pl.col("ticker").str.to_uppercase() == t)

    if query is not None and query.strip():
        q = query.strip()
        out = out.filter(
            pl.col("ticker").str.to_lowercase().str.contains(q.lower(), literal=True)
            | pl.col("name").fill_null("").str.to_lowercase().str.contains(q.lower(), literal=True)
        )

    market_list = markets
    if market_list is None and market is not None:
        market_list = [market]
    if market_list:
        lowered = [m.lower() for m in market_list]
        out = out.filter(pl.col("market").str.to_lowercase().is_in(lowered))

    if ticker_type is not None:
        out = out.filter(pl.col("type").str.to_uppercase() == ticker_type.upper())

    if exchange is not None:
        out = out.filter(
            pl.col("primary_exchange").fill_null("").str.to_uppercase() == exchange.upper()
        )

    if active is not None and "active" in out.columns:
        out = out.filter(pl.col("active") == active)

    if "ticker" in out.columns:
        out = out.sort("ticker")

    if limit is not None and limit > 0:
        out = out.head(limit)

    return out


def rows_for_config_add(df: pl.DataFrame) -> list[tuple[InstrumentType, str]]:
    """Convertit des lignes tickers en paires ``(InstrumentType, symbole_nu)``.

    Ignore crypto et markets inconnus. Déduplique en conservant l'ordre.
    """
    seen: set[tuple[str, str]] = set()
    result: list[tuple[InstrumentType, str]] = []

    if df.is_empty():
        return result

    for row in df.iter_rows(named=True):
        market = row.get("market")
        inst_type = market_to_instrument_type(str(market) if market is not None else None)
        if inst_type is None:
            continue
        raw = row.get("ticker")
        if not raw:
            continue
        symbol = strip_api_prefix(str(raw))
        if not symbol:
            continue
        key = (inst_type.value, symbol)
        if key in seen:
            continue
        seen.add(key)
        result.append((inst_type, symbol))

    return result
