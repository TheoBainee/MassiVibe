"""Gestion du cache agrégé (1 fichier Parquet par produit).

Le cache agrégé est le résultat de la fusion de tous les dumps bruts d'un
produit, dédupliqué sur ``(window_start, ticker)``. Il contient l'historique
continu prêt à être interrogé par la commande ``query``.

::

    data/aggregate/
    ├─ ES_continuous.parquet
    ├─ ES_continuous.meta.json
    ├─ NQ_continuous.parquet
    ├─ NQ_continuous.meta.json
    ├─ RTY_continuous.parquet
    ├─ RTY_continuous.meta.json
    └─ YM_continuous.parquet
    └─ YM_continuous.meta.json
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from massivibe.config import Settings
from massivibe.logging_setup import get_logger
from massivibe.storage.parquet_io import read_parquet, write_parquet

logger = get_logger("aggregate_cache")


def write_aggregate(
    df: pl.DataFrame,
    product_code: str,
    settings: Settings,
    source_dump_count: int = 0,
    dedup_removed_count: int = 0,
) -> Path:
    """Écrit le cache agrégé pour un produit.

    :param df: DataFrame agrégé (déjà dédupliqué et trié).
    :param product_code: Code produit (ex: "ES").
    :param settings: Configuration.
    :param source_dump_count: Nombre de dumps bruts fusionnés.
    :param dedup_removed_count: Nombre de doublons supprimés lors de la déduplication.
    :return: Le chemin du fichier Parquet écrit.
    """
    path = settings.aggregate_path(product_code)

    # Métadonnées spécifiques au cache agrégé
    extra_meta: dict[str, object] = {
        "product_code": product_code,
        "aggregated_at": True,  # sera remplacé par created_at du sidecar
        "source_dump_count": source_dump_count,
        "dedup_removed_count": dedup_removed_count,
    }

    # window_start_min / max pour audit rapide
    if "window_start" in df.columns and df.height > 0:
        ws_min = df["window_start"].min()
        ws_max = df["window_start"].max()
        if ws_min is not None:
            extra_meta["window_start_min"] = str(ws_min)
        if ws_max is not None:
            extra_meta["window_start_max"] = str(ws_max)

    # On retire le placeholder aggregated_at (created_at du sidecar suffit)
    extra_meta.pop("aggregated_at", None)

    write_parquet(df, path, **extra_meta)
    logger.info(
        f"Cache agrégé écrit: {path} ({df.height} lignes, "
        f"{source_dump_count} dumps fusionnés, {dedup_removed_count} doublons supprimés)"
    )
    return path


def read_aggregate(product_code: str, settings: Settings) -> pl.DataFrame:
    """Lit le cache agrégé d'un produit.

    :param product_code: Code produit (ex: "ES").
    :param settings: Configuration.
    :return: DataFrame Polars de l'historique continu.
    :raises FileNotFoundError: Si le cache agrégé n'existe pas.
    """
    path = settings.aggregate_path(product_code)
    return read_parquet(path)


def aggregate_exists(product_code: str, settings: Settings) -> bool:
    """Vérifie si le cache agrégé existe pour un produit.

    :param product_code: Code produit.
    :param settings: Configuration.
    :return: True si le fichier Parquet agrégé existe.
    """
    return settings.aggregate_path(product_code).exists()
