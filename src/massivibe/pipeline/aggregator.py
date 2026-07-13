"""Agrégation des dumps bruts en un cache agrégé continu.

L'agrégation fusionne tous les dumps bruts d'un produit (tous tickers, tous
runs confondus), déduplique les chandeliers sur ``(window_start, ticker)``,
et écrit le résultat dans le cache agrégé
``data/aggregate/{product_code}_continuous.parquet``.

**Déduplication** : on utilise ``keep="last"`` car les dumps sont lus par
ordre chronologique des ``run_ts`` — le dernier dump contient donc les
données les plus récentes (en cas de re-fetch d'un même chandelier).

**Cast Categorical** : les colonnes ``run_id``, ``ticker`` et ``product_code``
sont castées en ``Categorical`` (Polars) pour optimiser la mémoire et le
schéma Parquet. Ces colonnes ont une faible cardinalité (ex: ~10-20 tickers
distincts sur 2 ans de données 1m).

**Cast Int32** : les colonnes ``volume`` et ``transactions`` sont castées en
``Int32`` (au lieu du ``Int64`` retourné par l'API). Les valeurs réelles
tiennent largement en Int32 (max ~2.1 milliards) — le cast réduit l'empreinte
disque/mémoire de ~50% sur ces colonnes. Contrairement à la normalisation
tick_size, ce cast est **persisté dans le Parquet agrégé** : il est fait une
seule fois au moment de l'agrégation, pas à chaque lecture.

Note : la **normalisation tick_size** n'est PAS faite ici — elle se fait à
la lecture via ``query --normalize-tick-size``.
"""

from __future__ import annotations

import polars as pl

from massivibe.config import Settings
from massivibe.logging_setup import get_logger
from massivibe.storage.aggregate_cache import write_aggregate
from massivibe.storage.raw_dumps import read_all_runs

logger = get_logger("aggregator")

# Colonnes à caster en Categorical pour optimiser la mémoire.
# Ces colonnes ont une faible cardinalité (string répétées) — Categorical
# est beaucoup plus compact que Utf8 en mémoire et dans le Parquet.
_CATEGORICAL_COLS = ["run_id", "ticker", "product_code"]

# Colonnes entières à caster en Int32 (au lieu du Int64 de l'API).
# Les valeurs réelles (volume, transactions) tiennent largement en Int32
# (max ~2.1 milliards). Le cast est persisté dans le Parquet agrégé.
_INT32_COLS = ["volume", "transactions"]


def aggregate(product_code: str, settings: Settings) -> pl.DataFrame:
    """Agrège tous les dumps bruts d'un produit en un cache agrégé continu.

    Étapes :
    1. Lire tous les dumps bruts du produit (tous tickers, tous runs).
    2. Concaténer en un seul DataFrame.
    3. Caster ``run_id``, ``ticker``, ``product_code`` en ``Categorical``.
    4. Caster ``volume``, ``transactions`` en ``Int32`` (persisté dans le Parquet).
    5. Dédupliquer sur ``(window_start, ticker)`` avec ``keep="last"``.
    6. Trier par ``window_start``.
    7. Écrire le cache agrégé + sidecar ``.meta.json``.
    8. Logger le résumé (nb lignes avant/après, nb dumps fusionnés).

    :param product_code: Code produit (ex: "ES").
    :param settings: Configuration.
    :return: Le DataFrame agrégé écrit.
    """
    logger.info(f"Début de l'agrégation pour {product_code}")

    # 1. Lire tous les dumps bruts
    df = read_all_runs(product_code, settings)
    if df.is_empty():
        logger.warning(f"Aucun dump à agréger pour {product_code}")
        return df

    nb_before = df.height

    # 2. Cast Categorical sur les colonnes string répétées (optimisation mémoire)
    for col in _CATEGORICAL_COLS:
        if col in df.columns:
            df = df.with_columns(pl.col(col).cast(pl.Categorical))

    # 3. Cast Int32 sur les colonnes entières (volume, transactions)
    # L'API renvoie ces colonnes en Int64, mais les valeurs réelles tiennent
    # en Int32 (max ~2.1 milliards). Le cast est persisté dans le Parquet,
    # réduisant l'empreinte disque/mémoire de ~50% sur ces colonnes.
    for col in _INT32_COLS:
        if col in df.columns:
            df = df.with_columns(pl.col(col).cast(pl.Int32))

    # 4. Déduplication sur (window_start, ticker) — keep="last"
    # Les dumps sont lus par ordre chronologique des run_ts, donc le dernier
    # contient les données les plus récentes. En cas de doublon (même chandelier
    # re-téléchargé lors d'un run avec buffer de recouvrement), on garde la
    # version du run le plus récent.
    nb_before_dedup = df.height
    df = df.unique(subset=["window_start", "ticker"], keep="last")
    nb_after_dedup = df.height
    dedup_removed = nb_before_dedup - nb_after_dedup

    # 5. Tri par window_start (chronologique)
    if "window_start" in df.columns:
        df = df.sort("window_start")

    # 6. Écrire le cache agrégé + sidecar
    # On compte le nombre de dumps fusionnés (approximation via le nombre de
    # run_id distincts)
    source_dump_count = df["run_id"].n_unique() if "run_id" in df.columns else 0

    write_aggregate(
        df,
        product_code,
        settings,
        source_dump_count=source_dump_count,
        dedup_removed_count=dedup_removed,
    )

    logger.info(
        f"Agrégation {product_code} terminée: {nb_before} -> {nb_after_dedup} lignes "
        f"({dedup_removed} doublons supprimés, {source_dump_count} dumps fusionnés)"
    )

    return df
