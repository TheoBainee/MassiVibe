"""Recherche locale dans le cache tickers (Polars)."""

from __future__ import annotations

import re

import polars as pl

from myquantstore.instruments import InstrumentType

# Préfixes API Massive sur les tickers (forex/indices/options)
_API_PREFIX_RE = re.compile(r"^[CIO]:", re.IGNORECASE)

# market API → InstrumentType MyQuantStore (None = non supporté)
_MARKET_TO_TYPE: dict[str, InstrumentType | None] = {
    "stocks": InstrumentType.STOCKS,
    "otc": InstrumentType.STOCKS,
    "fx": InstrumentType.FOREX,
    "indices": InstrumentType.INDICES,
    "crypto": None,
    "options": InstrumentType.OPTIONS,
}

# market tickers → asset_class du cache types (join)
_MARKET_TO_ASSET_CLASS: dict[str, str] = {
    "stocks": "stocks",
    "otc": "stocks",
    "fx": "fx",
    "indices": "indices",
    "crypto": "crypto",
    "options": "options",
}

# Colonnes pour lesquelles on expose les valeurs distinctes (tickers values)
DISTINCT_VALUE_COLUMNS = (
    "market",
    "type",
    "primary_exchange",
    "currency_name",
)


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
    ``limit`` : plafond data optionnel (head). La CLI ``search --limit`` ne
    l'utilise pas : elle borne uniquement l'affichage via ``display_max_rows``.
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


def join_ticker_types(tickers_df: pl.DataFrame, types_df: pl.DataFrame) -> pl.DataFrame:
    """Left-join le cache types (``code/description``) sur les résultats tickers.

    Clé : ``type`` = ``code`` et ``market`` mappé vers ``asset_class``
    (``otc`` → ``stocks``). Ajoute ``type_description`` et ``type_locale``.
    Si le cache types est vide ou sans colonnes utiles, retourne ``tickers_df``
    inchangé.
    """
    if tickers_df.is_empty() or types_df.is_empty():
        return tickers_df
    if "type" not in tickers_df.columns:
        return tickers_df
    if "code" not in types_df.columns or "description" not in types_df.columns:
        return tickers_df

    # Prépare le côté types
    types_prep = types_df
    if "asset_class" not in types_prep.columns:
        types_prep = types_prep.with_columns(pl.lit(None).cast(pl.Utf8).alias("asset_class"))
    if "locale" not in types_prep.columns:
        types_prep = types_prep.with_columns(pl.lit(None).cast(pl.Utf8).alias("locale"))

    types_prep = types_prep.select(
        pl.col("code").cast(pl.Utf8).alias("_join_type"),
        pl.col("asset_class")
        .cast(pl.Utf8)
        .str.to_lowercase()
        .alias("_join_asset"),
        pl.col("description").cast(pl.Utf8).alias("type_description"),
        pl.col("locale").cast(pl.Utf8).alias("type_locale"),
    ).unique(subset=["_join_type", "_join_asset"], keep="first")

    # Prépare le côté tickers
    if "market" in tickers_df.columns:
        market_expr = (
            pl.col("market")
            .cast(pl.Utf8)
            .str.to_lowercase()
            .replace_strict(_MARKET_TO_ASSET_CLASS, default=None)
        )
    else:
        market_expr = pl.lit(None).cast(pl.Utf8)

    left = tickers_df.with_columns(
        pl.col("type").cast(pl.Utf8).alias("_join_type"),
        market_expr.alias("_join_asset"),
    )

    joined = left.join(types_prep, on=["_join_type", "_join_asset"], how="left")
    return joined.drop(["_join_type", "_join_asset"])


def distinct_column_values(
    df: pl.DataFrame,
    columns: tuple[str, ...] | list[str] = DISTINCT_VALUE_COLUMNS,
) -> dict[str, pl.DataFrame]:
    """Retourne, pour chaque colonne présente, un DF ``value, count`` trié.

    Les ``null`` / chaînes vides sont exclus. Colonnes absentes du DF sont omises.
    """
    result: dict[str, pl.DataFrame] = {}
    if df.is_empty():
        return result

    for col in columns:
        if col not in df.columns:
            continue
        counts = (
            df.select(pl.col(col).cast(pl.Utf8).alias("value"))
            .filter(pl.col("value").is_not_null() & (pl.col("value") != ""))
            .group_by("value")
            .len()
            .rename({"len": "count"})
            .sort(["count", "value"], descending=[True, False])
        )
        result[col] = counts
    return result


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
