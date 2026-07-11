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


def aggregate(product_code: str, settings: Settings) -> pl.DataFrame:
    """Agrège tous les dumps bruts d'un produit en un cache agrégé continu.

    Étapes :
    1. Lire tous les dumps bruts du produit (tous tickers, tous runs).
    2. Concaténer en un seul DataFrame.
    3. Caster ``run_id``, ``ticker``, ``product_code`` en ``Categorical``.
    4. Dédupliquer sur ``(window_start, ticker)`` avec ``keep="last"``.
    5. Trier par ``window_start``.
    6. Écrire le cache agrégé + sidecar ``.meta.json``.
    7. Logger le résumé (nb lignes avant/après, nb dumps fusionnés).

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

    # 3. Déduplication sur (window_start, ticker) — keep="last"
    # Les dumps sont lus par ordre chronologique des run_ts, donc le dernier
    # contient les données les plus récentes. En cas de doublon (même chandelier
    # re-téléchargé lors d'un run avec buffer de recouvrement), on garde la
    # version du run le plus récent.
    nb_before_dedup = df.height
    df = df.unique(subset=["window_start", "ticker"], keep="last")
    nb_after_dedup = df.height
    dedup_removed = nb_before_dedup - nb_after_dedup

    # 4. Tri par window_start (chronologique)
    if "window_start" in df.columns:
        df = df.sort("window_start")

    # 5. Écrire le cache agrégé + sidecar
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
