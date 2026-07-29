"""Gestion du cache agrégé (1 fichier Parquet par instrument × résolution).

Le cache agrégé est le résultat de la fusion de tous les dumps bruts d'un
instrument pour une résolution donnée, dédupliqué sur ``(window_start, ticker)``.
Il contient l'historique continu prêt à être interrogé par la commande ``query``.

Layout multi-type × multi-résolution ::

    data/aggregate/
    ├─ futures/
    │  ├─ ES/
    │  │  └─ 1min.parquet
    │  └─ NQ/
    │     └─ 1min.parquet
    └─ stocks/
       └─ AAPL/
          ├─ 1min.parquet   # Massive
          └─ 1day.parquet   # Yahoo (V1)

NB : le suffixe ``_continuous`` (futures-only) est abandonné au profit d'un
nom neutre — la logique de continu/rollover se fait à la query via la chaîne
d'instrument, pas au stockage.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from myquantstore.config import Settings
from myquantstore.instruments import DEFAULT_RESOLUTION, Instrument
from myquantstore.logging_setup import get_logger
from myquantstore.storage.parquet_io import read_parquet, write_parquet

logger = get_logger("aggregate_cache")


def write_aggregate(
    df: pl.DataFrame,
    instrument: Instrument,
    settings: Settings,
    source_dump_count: int = 0,
    dedup_removed_count: int = 0,
    resolution: str = DEFAULT_RESOLUTION,
    source: str = "massive",
) -> Path:
    """Écrit le cache agrégé pour un instrument × résolution.

    :param df: DataFrame agrégé (déjà dédupliqué et trié).
    :param instrument: Instrument cible.
    :param settings: Configuration.
    :param source_dump_count: Nombre de dumps bruts fusionnés.
    :param dedup_removed_count: Nombre de doublons supprimés.
    :param resolution: Résolution de stockage (``1min``, ``1day``, …).
    :param source: Provenance des données (``massive``, ``yahoo``, …).
    :return: Le chemin du fichier Parquet écrit.
    """
    path = settings.aggregate_path(instrument, resolution=resolution)

    extra_meta: dict[str, object] = {
        "instrument_type": instrument.type.value,
        "symbol": instrument.symbol,
        "resolution": resolution,
        "source": source,
        "source_dump_count": source_dump_count,
        "dedup_removed_count": dedup_removed_count,
    }

    if "window_start" in df.columns and df.height > 0:
        ws_min = df["window_start"].min()
        ws_max = df["window_start"].max()
        if ws_min is not None:
            extra_meta["window_start_min"] = str(ws_min)
        if ws_max is not None:
            extra_meta["window_start_max"] = str(ws_max)

    write_parquet(df, path, **extra_meta)
    logger.info(
        f"Cache agrégé écrit: {path} ({df.height} lignes, "
        f"{source_dump_count} dumps fusionnés, {dedup_removed_count} doublons supprimés)"
    )
    return path


def read_aggregate(
    instrument: Instrument,
    settings: Settings,
    resolution: str = DEFAULT_RESOLUTION,
) -> pl.DataFrame:
    """Lit le cache agrégé d'un instrument × résolution.

    :raises FileNotFoundError: Si le cache agrégé n'existe pas.
    """
    path = settings.aggregate_path(instrument, resolution=resolution)
    return read_parquet(path)


def aggregate_exists(
    instrument: Instrument,
    settings: Settings,
    resolution: str = DEFAULT_RESOLUTION,
) -> bool:
    """Vérifie si le cache agrégé existe pour un instrument × résolution."""
    return settings.aggregate_path(instrument, resolution=resolution).exists()


def list_aggregate_resolutions(instrument: Instrument, settings: Settings) -> list[str]:
    """Liste les résolutions d'agrégat présentes pour un instrument."""
    symbol_dir = settings.aggregate_dir() / instrument.path_segment / instrument.symbol
    if not symbol_dir.is_dir():
        return []
    return sorted(p.stem for p in symbol_dir.glob("*.parquet"))
