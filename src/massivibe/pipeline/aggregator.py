"""Agrégation des dumps bruts en un cache agrégé (générique multi-type).

L'agrégation fusionne tous les dumps bruts d'un instrument (tous tickers, tous
runs confondus), déduplique les chandeliers sur ``(window_start, ticker)``,
et écrit le résultat dans ``data/aggregate/{type}/{symbol}.parquet``.

Cette fonction est **générique** — elle ne contient aucune logique de rollover
(spécifique futures). Le stitching continu/rollover se fait à la query via la
chaîne d'instrument (:mod:`massivibe.chains`).

**Déduplication** : ``keep="last"`` car les dumps sont lus par ordre
chronologique des ``run_ts`` — le dernier dump contient les données les plus
récentes (en cas de re-fetch d'un même chandelier).

**Cast Categorical** : ``run_id``, ``ticker``, ``symbol``, ``instrument_type``,
``product_code`` → ``Categorical`` (faible cardinalité, compact en Parquet).

**Cast Int32** : ``volume``, ``transactions`` → ``Int32`` (les valeurs réelles
tiennent en Int32). Ce cast est **persisté dans le Parquet** agrégé.
"""

from __future__ import annotations

import polars as pl

from massivibe.config import Settings
from massivibe.instruments import Instrument
from massivibe.logging_setup import get_logger
from massivibe.storage.aggregate_cache import write_aggregate
from massivibe.storage.raw_dumps import read_all_runs

logger = get_logger("aggregator")

# Colonnes à caster en Categorical (faible cardinalité).
_CATEGORICAL_COLS = ["run_id", "ticker", "symbol", "instrument_type", "product_code"]

# Colonnes entières à caster en Int32 (au lieu du Int64 de l'API).
_INT32_COLS = ["volume", "transactions"]


def aggregate(instrument: Instrument, settings: Settings) -> pl.DataFrame:
    """Agrège tous les dumps bruts d'un instrument en un cache agrégé.

    :param instrument: Instrument cible.
    :param settings: Configuration.
    :return: Le DataFrame agrégé écrit.
    """
    logger.info(f"Début de l'agrégation pour {instrument.key}")

    df = read_all_runs(instrument, settings)
    if df.is_empty():
        logger.warning(f"Aucun dump à agréger pour {instrument.key}")
        return df

    nb_before = df.height

    # Cast Categorical sur les colonnes string répétées (optimisation mémoire)
    for col in _CATEGORICAL_COLS:
        if col in df.columns:
            df = df.with_columns(pl.col(col).cast(pl.Categorical))

    # Cast Int32 sur les colonnes entières (volume, transactions)
    for col in _INT32_COLS:
        if col in df.columns:
            df = df.with_columns(pl.col(col).cast(pl.Int32))

    # Déduplication sur (window_start, ticker) — keep="last"
    nb_before_dedup = df.height
    df = df.unique(subset=["window_start", "ticker"], keep="last")
    nb_after_dedup = df.height
    dedup_removed = nb_before_dedup - nb_after_dedup

    # Tri par window_start (chronologique)
    if "window_start" in df.columns:
        df = df.sort("window_start")

    source_dump_count = df["run_id"].n_unique() if "run_id" in df.columns else 0

    write_aggregate(
        df,
        instrument,
        settings,
        source_dump_count=source_dump_count,
        dedup_removed_count=dedup_removed,
    )

    logger.info(
        f"Agrégation {instrument.key} terminée: {nb_before} -> {nb_after_dedup} lignes "
        f"({dedup_removed} doublons supprimés, {source_dump_count} dumps fusionnés)"
    )

    return df
