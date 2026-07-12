"""Requêtes sur l'historique continu (commande ``query``).

La fonction :func:`query` interroge le cache agrégé d'un produit et retourne
un DataFrame Polars filtré par plage temporelle. Trois flags sont disponibles :

- ``adjust_rollover`` (``--adjust``) : ajustement de rollover (stub ``NotImplementedError``).
- ``normalize_tick_size`` (``--normalize-tick-size``) : conversion prix → multiples
  entiers de tick size (``Int32``).
- ``check_ticksize_accuracy`` (``--check-ticksize-accuracy``) : analyse la conformité
  des prix au tick size et affiche un bilan (read-only, ne modifie pas les données).

**Incompatibilité** : ``normalize_tick_size`` et ``adjust_rollover`` sont mutuellement
exclusifs — le CLI rejette la combinaison avec une ``ValueError``.
"""

from __future__ import annotations

from datetime import datetime

import polars as pl
from rich.console import Console
from rich.table import Table

from massivibe.config import Settings
from massivibe.contracts.rollover import RolloverChain
from massivibe.logging_setup import get_logger
from massivibe.storage.aggregate_cache import read_aggregate

logger = get_logger("query")

# Colonnes de prix concernées par la normalisation et le test de qualité
_PRICE_COLS = ["open", "high", "low", "close", "settlement_price"]

# Seuils du bilan de qualité (codés en dur)
DATA_QUALITY_WARNING_THRESHOLD = 0.01  # ≥1% -> statut ATTENTION (WARNING log)
DATA_QUALITY_ERROR_THRESHOLD = 0.05    # ≥5% -> statut ERREUR (ERROR log, exit code 1)


class DataQualityError(Exception):
    """Levée quand le bilan de qualité tick_size dépasse le seuil d'erreur.

    Note : cette exception n'est levée QUE dans le contexte de la normalisation
    automatique (si on décide plus tard de bloquer). Pour ``--check-ticksize-accuracy``,
    on affiche seulement le bilan + exit code 1 sans lever d'exception.
    """


def query(
    product_code: str,
    settings: Settings,
    chain: RolloverChain,
    start: datetime | None = None,
    end: datetime | None = None,
    adjust_rollover: bool = False,
    normalize_tick_size: bool = False,
    check_ticksize_accuracy: bool = False,
    limit: int | None = None,
) -> pl.DataFrame:
    """Interroge l'historique continu d'un produit.

    :param product_code: Code produit (ex: "ES").
    :param settings: Configuration.
    :param chain: RolloverChain du produit (pour tick_size et ajustement).
    :param start: Date/time de début (inclusive). Si None, depuis le début.
    :param end: Date/time de fin (inclusive). Si None, jusqu'à la fin.
    :param adjust_rollover: Si True, ajuste les gaps de rollover (stub NotImplementedError).
    :param normalize_tick_size: Si True, convertit OHLC + settlement en Int32 (multiples de tick).
    :param check_ticksize_accuracy: Si True, analyse la conformité au tick size et affiche un bilan.
    :param limit: Nombre max de lignes à retourner.
    :return: DataFrame Polars de l'historique (filtré et éventuellement normalisé).
    :raises ValueError: Si ``adjust_rollover`` et ``normalize_tick_size`` sont both True.
    :raises NotImplementedError: Si ``adjust_rollover=True`` (stub).
    """
    # --- Incompatibilité mutuelle ---
    if adjust_rollover and normalize_tick_size:
        raise ValueError(
            "normalize_tick_size et adjust_rollover sont incompatibles. "
            "L'ajustement de rollover futur devra calculer en prix réels (Float64) "
            "ou en unités de tick (Int32), mais pas les deux simultanément."
        )

    # --- Lecture du cache agrégé ---
    df = read_aggregate(product_code, settings)

    # --- Cast des colonnes entières en Int32 ---
    # volume et transactions sont stockés en Int64 (type API), mais les valeurs
    # réelles tiennent largement en Int32 (max ~2.1 milliards). Le cast en Int32
    # réduit l'empreinte mémoire de ~50% sur ces colonnes.
    int32_cols = [c for c in ("volume", "transactions") if c in df.columns]
    if int32_cols:
        df = df.with_columns([pl.col(c).cast(pl.Int32) for c in int32_cols])

    # --- Filtrage temporel ---
    if start is not None:
        df = df.filter(pl.col("window_start") >= start)
    if end is not None:
        df = df.filter(pl.col("window_start") <= end)

    # --- Ajustement de rollover (stub) ---
    if adjust_rollover:
        raise NotImplementedError(
            "adjust_rollover=True non implémenté — méthode d'ajustement à définir. "
            "Voir §6.4 de la documentation technique."
        )

    # --- Bilan qualité tick size (read-only, affiche un bilan) ---
    if check_ticksize_accuracy:
        bilan = check_ticksize_accuracy_fn(df, chain, settings.data_quality_trigger)
        _print_quality_bilan(product_code, bilan)

    # --- Normalisation tick size (à la lecture) ---
    if normalize_tick_size:
        df = _normalize_tick_size(df, chain)

    # --- Limit ---
    if limit is not None and limit > 0:
        df = df.head(limit)

    return df


def _normalize_tick_size(df: pl.DataFrame, chain: RolloverChain) -> pl.DataFrame:
    """Convertit les colonnes de prix en multiples entiers de tick size (Int32).

    Pour chaque ticker, on divise les 5 colonnes de prix par le ``trade_tick_size``
    du contrat (récupéré via la RolloverChain), on arrondit, et on cast en Int32.

    :param df: DataFrame avec prix en Float64.
    :param chain: RolloverChain pour récupérer les tick sizes.
    :return: DataFrame avec prix en Int32.
    """
    logger.info("Normalisation tick_size: conversion Float64 -> Int32")

    # Récupérer les tickers uniques présents dans le DataFrame
    tickers = df["ticker"].unique().to_list()

    for ticker in tickers:
        tick = chain.tick_size_for_ticker(ticker)
        if tick <= 0:
            logger.warning(f"tick_size invalide pour {ticker} ({tick}) — skip normalisation")
            continue

        for col in _PRICE_COLS:
            if col not in df.columns:
                continue
            # Division + arrondi, conditionnel au ticker
            # On garde le type Float64 pendant la division (sinon les autres tickers seraient cassés)
            df = df.with_columns(
                pl.when(pl.col("ticker") == ticker)
                .then((pl.col(col) / tick).round())
                .otherwise(pl.col(col))
                .alias(col)
            )

    # Cast final en Int32 pour toutes les colonnes de prix
    # (toutes les valeurs sont maintenant des entiers arrondis, même si le dtype est encore Float64)
    for col in _PRICE_COLS:
        if col in df.columns:
            df = df.with_columns(pl.col(col).cast(pl.Int32))

    logger.info(f"Normalisation terminée pour {len(tickers)} ticker(s)")
    return df


def check_ticksize_accuracy_fn(
    df: pl.DataFrame,
    chain: RolloverChain,
    trigger: float,
) -> pl.DataFrame:
    """Analyse la conformité des prix au tick size et retourne un bilan par ticker.

    Pour chaque ticker et chaque colonne de prix, on compte le nombre de
    valeurs non conformes : ``ABS((prix/tick) - round(prix/tick)) > trigger * tick``.

    :param df: DataFrame à analyser.
    :param chain: RolloverChain pour récupérer les tick sizes.
    :param trigger: Tolérance relative (ex: 0.1 = 10% d'un tick).
    :return: DataFrame bilan avec colonnes : ticker, tick_size, total_candles,
        non_conformes, ratio, statut.
    """
    rows: list[dict[str, object]] = []
    tickers = df["ticker"].unique().to_list()

    for ticker in tickers:
        tick = chain.tick_size_for_ticker(ticker)
        if tick <= 0:
            logger.warning(f"tick_size invalide pour {ticker} — skip analyse")
            continue

        subset = df.filter(pl.col("ticker") == ticker)
        total = subset.height

        # Compter les non-conformes sur toutes les colonnes de prix
        # Une ligne est non-conforme si AU MOINS UNE colonne de prix l'est
        # On construit un masque OR sur toutes les colonnes
        bad_mask = pl.lit(False)
        for col in _PRICE_COLS:
            if col not in subset.columns:
                continue
            # ABS((p / tick) - round(p / tick)) > trigger * tick
            col_bad = (
                (pl.col(col) / tick - (pl.col(col) / tick).round()).abs() > trigger * tick
            )
            bad_mask = bad_mask | col_bad

        nb_bad = subset.filter(bad_mask).height
        ratio = nb_bad / total if total > 0 else 0.0

        # Détermination du statut
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

    bilan = pl.DataFrame(rows)
    return bilan


def _print_quality_bilan(product_code: str, bilan: pl.DataFrame) -> None:
    """Affiche le bilan de qualité tick_size sur stdout (table riche).

    :param product_code: Code produit.
    :param bilan: DataFrame bilan retourné par :func:`check_ticksize_accuracy_fn`.
    """
    if bilan.is_empty():
        logger.info(f"Bilan qualité tick_size pour {product_code}: aucun ticker à analyser")
        return

    console = Console()
    table = Table(title=f"== {product_code} — Bilan qualité tick size ==")
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

        # Couleur selon le statut
        if statut == "OK":
            statut_str = f"[green]{statut}[/green]"
            logger.info(f"Qualité tick_size {ticker}: {bad}/{candles} non conformes ({ratio:.4%}) — OK")
        elif statut == "ATTENTION":
            statut_str = f"[yellow]{statut}[/yellow]"
            logger.warning(
                f"Qualité tick_size {ticker}: {bad}/{candles} non conformes ({ratio:.2%}) — ATTENTION"
            )
        else:  # ERREUR
            statut_str = f"[red]{statut}[/red]"
            logger.error(
                f"Qualité tick_size {ticker}: {bad}/{candles} non conformes ({ratio:.2%}) — ERREUR"
            )

        table.add_row(str(ticker), str(tick), str(candles), str(bad), f"{ratio:.4%}", statut_str)

        total_candles += candles
        total_bad += bad

    # Ligne TOTAL
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
