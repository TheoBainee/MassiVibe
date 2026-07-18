"""Requêtes sur l'historique continu (commande ``query``).

La fonction :func:`query` interroge le cache agrégé d'un instrument et retourne
un DataFrame Polars filtré par plage temporelle. Plusieurs transformations
sont disponibles :

- ``k_minutes`` (``--timescale-unit/nb``) : rééchantillonnage des candles 1min
  en candles k-min. Voir :mod:`massivibe.query.resampler`.
- ``intraday_begin`` / ``intraday_end`` : filtrage des candles par heure du jour
  (supporte le wrap-around, ex: 20:00-04:00).
- ``no_split`` (``--no-split``) : pour stocks, désactive l'ajustement split
  (activé par défaut). Les prix bruts sont stockés ; l'ajustement split se fait
  ici via le cache corporate actions.
- ``adjust_rollover`` (``--adjust``) : ajustement de rollover / dividend
  (``NotImplementedError`` — planifié).
- ``normalize_tick_size`` (``--normalize-tick-size``) : conversion prix →
  multiples entiers de tick size (``Int32``). Futures uniquement (requiert
  ``chain`` avec ``tick_size_for_ticker``).
- ``check_ticksize_accuracy`` (``--check-ticksize-accuracy``) : analyse la
  conformité des prix au tick size (read-only). Futures uniquement.

**Multi-type** : ``chain`` est optionnel (:class:`massivibe.chains.InstrumentChain`).
Pour forex/stocks/indices, on peut passer ``chain=None`` ou une
``SingleSymbolChain``. ``normalize_tick_size`` / ``check_ticksize_accuracy``
requièrent une chaîne avec un ``tick_size_for_ticker`` non nul (futures).
"""

from __future__ import annotations

from datetime import UTC, datetime, time

import polars as pl
from rich.console import Console
from rich.table import Table

from massivibe.chains import InstrumentChain
from massivibe.config import Settings
from massivibe.instruments import Instrument, InstrumentType
from massivibe.logging_setup import get_logger
from massivibe.query.adjust import apply_split_adjustment
from massivibe.query.resampler import filter_intraday, resample_ohlcv
from massivibe.storage.aggregate_cache import read_aggregate

logger = get_logger("query")

# Colonnes de prix concernées par la normalisation et le test de qualité
_PRICE_COLS = ["open", "high", "low", "close", "settlement_price"]

# Seuils du bilan de qualité (codés en dur)
DATA_QUALITY_WARNING_THRESHOLD = 0.01  # ≥1% -> statut ATTENTION (WARNING log)
DATA_QUALITY_ERROR_THRESHOLD = 0.05  # ≥5% -> statut ERREUR (ERROR log, exit code 1)


class DataQualityError(Exception):
    """Levée quand le bilan de qualité tick_size dépasse le seuil d'erreur."""


def query(
    instrument: Instrument,
    settings: Settings,
    chain: InstrumentChain | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    k_minutes: int = 1,
    intraday_begin: time | None = None,
    intraday_end: time | None = None,
    adjust_rollover: bool = False,
    normalize_tick_size: bool = False,
    check_ticksize_accuracy: bool = False,
    no_split: bool = False,
    limit: int | None = None,
) -> pl.DataFrame:
    """Interroge l'historique continu d'un instrument.

    :param instrument: Instrument cible.
    :param settings: Configuration.
    :param chain: Chaîne d'instrument (RolloverChain futures, SingleSymbolChain
        autres). Optionnel — requis uniquement pour ``normalize_tick_size`` et
        ``check_ticksize_accuracy``.
    :param start: Date/time de début (inclusive). Si None, depuis le début.
    :param end: Date/time de fin (inclusive). Si None, jusqu'à la fin.
    :param k_minutes: Rééchantillonnage en k minutes (1 = pas de resampling).
    :param intraday_begin: Heure de début intraday (HH:MM). Wrap-around supporté.
    :param intraday_end: Heure de fin intraday (HH:MM).
    :param adjust_rollover: Si True, ajuste les gaps de rollover / dividend
        (``NotImplementedError`` — planifié).
    :param normalize_tick_size: Si True, convertit OHLC + settlement en Int32
        (multiples de tick). Requiert ``chain`` (futures).
    :param check_ticksize_accuracy: Si True, analyse la conformité au tick size.
        Requiert ``chain`` (futures).
    :param no_split: Si True, désactive l'ajustement split (stocks). Par défaut
        (False), l'ajustement split est appliqué pour les stocks via le cache
        corporate actions.
    :param limit: Plafond data optionnel (head). La CLI ``query --limit`` ne
        l'utilise pas : elle borne uniquement l'affichage via ``display_max_rows``.
    :return: DataFrame Polars de l'historique (filtré, éventuellement ajusté,
        resamplé et normalisé).
    :raises ValueError: Si ``adjust_rollover`` et ``normalize_tick_size`` sont both True.
    :raises ValueError: Si ``intraday_begin == intraday_end``.
    :raises ValueError: Si ``k_minutes < 1``.
    :raises ValueError: Si ``normalize_tick_size``/``check_ticksize_accuracy`` sans ``chain``.
    :raises NotImplementedError: Si ``adjust_rollover=True`` (planifié).
    """
    # --- Incompatibilité mutuelle ---
    if adjust_rollover and normalize_tick_size:
        raise ValueError(
            "normalize_tick_size et adjust_rollover sont incompatibles. "
            "L'ajustement futur devra calculer en prix réels (Float64) "
            "ou en unités de tick (Int32), mais pas les deux simultanément."
        )

    # --- Validation intraday ---
    if (
        intraday_begin is not None
        and intraday_end is not None
        and intraday_begin == intraday_end
    ):
        raise ValueError(
            "intraday_begin et intraday_end doivent être différents. "
            "Pour ne pas filtrer, omettez les deux paramètres."
        )

    # --- Validation k_minutes ---
    if k_minutes < 1:
        raise ValueError(f"k_minutes doit être >= 1 (reçu: {k_minutes})")

    # --- Validation chain requise pour tick_size ---
    if (normalize_tick_size or check_ticksize_accuracy) and chain is None:
        raise ValueError(
            "normalize_tick_size et check_ticksize_accuracy requièrent une chaîne "
            "(chain) avec tick_size_for_ticker — passez une RolloverChain (futures)."
        )

    # --- Lecture du cache agrégé ---
    df = read_aggregate(instrument, settings)

    # --- Filtrage temporel (start/end datetime) ---
    # On strip la timezone des deux côtés (colonne + paramètre) pour comparer naive vs naive.
    if start is not None:
        start_naive = start.astimezone(UTC).replace(tzinfo=None) if start.tzinfo is not None else start
        df = df.filter(pl.col("window_start").dt.replace_time_zone(None) >= start_naive)
    if end is not None:
        end_naive = end.astimezone(UTC).replace(tzinfo=None) if end.tzinfo is not None else end
        df = df.filter(pl.col("window_start").dt.replace_time_zone(None) <= end_naive)

    # --- Filtrage intraday (par heure du jour) ---
    if intraday_begin is not None and intraday_end is not None:
        df = filter_intraday(df, intraday_begin, intraday_end)

    # --- Ajustement split (stocks, activé par défaut — --no-split désactive) ---
    if instrument.type == InstrumentType.STOCKS and not no_split:
        df = _apply_stock_split_adjustment(df, instrument, settings)

    # --- Ajustement de rollover / dividend (stub) ---
    if adjust_rollover:
        raise NotImplementedError(
            "adjust_rollover=True non implémenté — l'ajustement de rollover "
            "(futures) et dividend (stocks) est planifié. Voir la spécification "
            "fonctionnelle."
        )

    # --- Bilan qualité tick size (read-only, affiche un bilan) ---
    if check_ticksize_accuracy and chain is not None:
        bilan = check_ticksize_accuracy_fn(df, chain, settings.data_quality_trigger)
        _print_quality_bilan(str(instrument), bilan)

    # --- Normalisation tick size (à la lecture) ---
    if normalize_tick_size and chain is not None:
        df = _normalize_tick_size(df, chain)

    # --- Rééchantillonnage (resampling k-min) ---
    if k_minutes > 1:
        df = resample_ohlcv(df, k_minutes, intraday_begin, intraday_end)

    # --- Limit ---
    if limit is not None and limit > 0:
        df = df.head(limit)

    return df


def _apply_stock_split_adjustment(df: pl.DataFrame, instrument: Instrument, settings: Settings) -> pl.DataFrame:
    """Applique l'ajustement split pour un stock via son cache corporate actions."""
    from massivibe.corporate_actions.cache import CorporateActionsCache

    try:
        splits_cache = CorporateActionsCache(instrument.symbol, "splits", settings)
        splits = splits_cache.get()
        return apply_split_adjustment(df, splits)
    except FileNotFoundError:
        logger.warning(
            f"Pas de cache splits pour {instrument.symbol} — prix non ajustés (bruts). "
            "Lancez 'massivibe fetch' pour peupler le cache splits."
        )
        return df


def _normalize_tick_size(df: pl.DataFrame, chain: InstrumentChain) -> pl.DataFrame:
    """Convertit les colonnes de prix en multiples entiers de tick size (Int32).

    Pour chaque ticker, on divise les colonnes de prix par le ``trade_tick_size``
    du contrat (via la chaîne), on arrondit, et on cast en Int32. Si le tick
    size est 0.0 (SingleSymbolChain), la normalisation est skippée pour ce ticker.
    """
    logger.info("Normalisation tick_size: conversion Float64 -> Int32")

    if "ticker" not in df.columns:
        return df

    tickers = df["ticker"].unique().to_list()

    for ticker in tickers:
        tick = chain.tick_size_for_ticker(ticker)
        if tick <= 0:
            logger.debug(f"tick_size=0 pour {ticker} — skip normalisation (type non-futures)")
            continue

        for col in _PRICE_COLS:
            if col not in df.columns:
                continue
            df = df.with_columns(
                pl.when(pl.col("ticker") == ticker)
                .then((pl.col(col) / tick).round())
                .otherwise(pl.col(col))
                .alias(col)
            )

    # Cast final en Int32 pour toutes les colonnes de prix présentes
    for col in _PRICE_COLS:
        if col in df.columns:
            df = df.with_columns(pl.col(col).cast(pl.Int32))

    logger.info(f"Normalisation terminée pour {len(tickers)} ticker(s)")
    return df


def check_ticksize_accuracy_fn(
    df: pl.DataFrame,
    chain: InstrumentChain,
    trigger: float,
) -> pl.DataFrame:
    """Analyse la conformité des prix au tick size et retourne un bilan par ticker.

    Pour chaque ticker et chaque colonne de prix, on compte le nombre de
    valeurs non conformes : ``ABS((prix/tick) - round(prix/tick)) > trigger * tick``.
    Les tickers avec ``tick_size=0`` (non-futures) sont skippés.
    """
    rows: list[dict[str, object]] = []
    if "ticker" not in df.columns:
        return pl.DataFrame()
    tickers = df["ticker"].unique().to_list()

    for ticker in tickers:
        tick = chain.tick_size_for_ticker(ticker)
        if tick <= 0:
            continue

        subset = df.filter(pl.col("ticker") == ticker)
        total = subset.height

        bad_mask = pl.lit(False)
        for col in _PRICE_COLS:
            if col not in subset.columns:
                continue
            col_bad = (
                (pl.col(col) / tick - (pl.col(col) / tick).round()).abs() > trigger * tick
            )
            bad_mask = bad_mask | col_bad

        nb_bad = subset.filter(bad_mask).height
        ratio = nb_bad / total if total > 0 else 0.0

        if ratio >= DATA_QUALITY_ERROR_THRESHOLD:
            statut = "ERREUR"
        elif ratio >= DATA_QUALITY_WARNING_THRESHOLD:
            statut = "ATTENTION"
        else:
            statut = "OK"

        rows.append(
            {
                "ticker": ticker,
                "tick_size": tick,
                "total_candles": total,
                "non_conformes": nb_bad,
                "ratio": ratio,
                "statut": statut,
            }
        )

    return pl.DataFrame(rows)


def _print_quality_bilan(label: str, bilan: pl.DataFrame) -> None:
    """Affiche le bilan de qualité tick_size sur stdout (table riche)."""
    if bilan.is_empty():
        logger.info(f"Bilan qualité tick_size pour {label}: aucun ticker à analyser")
        return

    console = Console()
    table = Table(title=f"== {label} — Bilan qualité tick size ==")
    table.add_column("ticker", style="cyan")
    table.add_column("tick_size", justify="right")
    table.add_column("total_candles", justify="right")
    table.add_column("non_conformes", justify="right")
    table.add_column("ratio", justify="right")
    table.add_column("statut")

    total_candles = 0
    total_bad = 0

    for row in bilan.iter_rows(named=True):
        ticker = row["ticker"]
        tick = row["tick_size"]
        candles = row["total_candles"]
        bad = row["non_conformes"]
        ratio = row["ratio"]
        statut = row["statut"]

        if statut == "OK":
            statut_str = f"[green]{statut}[/green]"
            logger.info(f"Qualité tick_size {ticker}: {bad}/{candles} non conformes ({ratio:.4%}) — OK")
        elif statut == "ATTENTION":
            statut_str = f"[yellow]{statut}[/yellow]"
            logger.warning(
                f"Qualité tick_size {ticker}: {bad}/{candles} non conformes ({ratio:.2%}) — ATTENTION"
            )
        else:
            statut_str = f"[red]{statut}[/red]"
            logger.error(
                f"Qualité tick_size {ticker}: {bad}/{candles} non conformes ({ratio:.2%}) — ERREUR"
            )

        table.add_row(str(ticker), str(tick), str(candles), str(bad), f"{ratio:.4%}", statut_str)
        total_candles += candles
        total_bad += bad

    total_ratio = total_bad / total_candles if total_candles > 0 else 0.0
    if total_ratio >= DATA_QUALITY_ERROR_THRESHOLD:
        total_statut = "[red]ERREUR[/red]"
    elif total_ratio >= DATA_QUALITY_WARNING_THRESHOLD:
        total_statut = "[yellow]ATTENTION[/yellow]"
    else:
        total_statut = "[green]OK[/green]"

    table.add_row(
        "[bold]TOTAL[/bold]",
        "—",
        str(total_candles),
        str(total_bad),
        f"{total_ratio:.4%}",
        total_statut,
    )

    console.print(table)
