"""Gestion des dumps bruts de l'API (1 fichier Parquet par contrat et par run).

Structure des dumps bruts :

::

    data/raw/
    ├─ ES/
    │  ├─ ESM5/
    │  │  ├─ 20260704T180000.parquet       # dump du run du 2026-07-04
    │  │  ├─ 20260704T180000.meta.json     # sidecar
    │  │  ├─ 20260711T183000.parquet       # dump du run du 2026-07-11
    │  │  └─ 20260711T183000.meta.json
    │  ├─ ESU5/
    │  │  └─ ...
    │  ...
    ├─ NQ/
    │  ...

Chaque fichier est **immuable** (jamais écrasé) — un nouveau run crée un
nouveau fichier avec un ``run_ts`` unique. Cela permet l'audit et la
re-agrégation à partir des dumps bruts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from massivibe.config import Settings
from massivibe.logging_setup import get_logger
from massivibe.storage.parquet_io import read_parquet, write_parquet

logger = get_logger("raw_dumps")


def save_raw_dump(
    df: pl.DataFrame,
    product_code: str,
    ticker: str,
    run_ts: str,
    settings: Settings,
    source_url: str = "",
    page_count: int = 0,
) -> Path:
    """Sauvegarde un dump brut d'un contrat pour un run donné.

    Le fichier est écrit dans ``data/raw/{product_code}/{ticker}/{run_ts}.parquet``
    avec son sidecar ``.meta.json`` contenant les métadonnées du run.

    :param df: DataFrame des chandeliers OHLCV à sauvegarder.
    :param product_code: Code produit (ex: "ES").
    :param ticker: Ticker du contrat (ex: "ESM5").
    :param run_ts: Identifiant du run (format YYYYMMDDTHHMMSS).
    :param settings: Configuration (pour data_dir, raw_dumps_subdir).
    :param source_url: URL source de l'appel API (pour audit).
    :param page_count: Nombre de pages paginées pour ce dump.
    :return: Le chemin du fichier Parquet écrit.
    """
    path = settings.raw_dump_path(product_code, ticker, run_ts)

    # Métadonnées spécifiques au dump brut
    extra_meta: dict[str, object] = {
        "product_code": product_code,
        "ticker": ticker,
        "run_ts": run_ts,
        "source_url": source_url,
        "page_count": page_count,
    }

    # window_start_min / max pour audit rapide (si la colonne existe)
    if "window_start" in df.columns and df.height > 0:
        ws_min = df["window_start"].min()
        ws_max = df["window_start"].max()
        # Conversion en ISO string pour JSON
        if ws_min is not None:
            extra_meta["window_start_min"] = str(ws_min)
        if ws_max is not None:
            extra_meta["window_start_max"] = str(ws_max)

    write_parquet(df, path, **extra_meta)
    logger.info(f"Dump brut sauvegardé: {path} ({df.height} chandeliers)")
    return path


def list_runs(product_code: str, ticker: str, settings: Settings) -> list[str]:
    """Liste tous les ``run_ts`` disponibles pour un contrat donné.

    :param product_code: Code produit (ex: "ES").
    :param ticker: Ticker du contrat (ex: "ESM5").
    :param settings: Configuration.
    :return: Liste triée des run_ts (format YYYYMMDDTHHMMSS) par ordre chronologique.
    """
    ticker_dir = settings.raw_dumps_dir() / product_code / ticker
    if not ticker_dir.exists():
        return []

    run_ts_list = sorted(
        f.stem for f in ticker_dir.glob("*.parquet") if f.suffix == ".parquet"
    )
    return run_ts_list


def list_tickers(product_code: str, settings: Settings) -> list[str]:
    """Liste tous les tickers ayant au moins un dump brut pour un produit.

    :param product_code: Code produit (ex: "ES").
    :param settings: Configuration.
    :return: Liste triée des tickers (ex: ["ESH5", "ESM5", "ESU5"]).
    """
    product_dir = settings.raw_dumps_dir() / product_code
    if not product_dir.exists():
        return []

    tickers = sorted(d.name for d in product_dir.iterdir() if d.is_dir())
    return tickers


def read_all_runs(product_code: str, settings: Settings) -> pl.DataFrame:
    """Lit et concatène tous les dumps bruts d'un produit (tous tickers, tous runs).

    Utilisé par l'agrégateur pour reconstruire l'historique complet à partir
    des dumps bruts. Les dumps sont lus par ordre chronologique des ``run_ts``
    pour que la déduplication ``keep="last"`` conserve les données les plus récentes.

    :param product_code: Code produit (ex: "ES").
    :param settings: Configuration.
    :return: DataFrame Polars concaténé avec une colonne ``run_id`` (le run_ts source).
    """
    tickers = list_tickers(product_code, settings)
    if not tickers:
        logger.warning(f"Aucun dump brut trouvé pour {product_code}")
        return pl.DataFrame()

    frames: list[pl.DataFrame] = []

    for ticker in tickers:
        run_ts_list = list_runs(product_code, ticker, settings)
        for run_ts in run_ts_list:
            path = settings.raw_dump_path(product_code, ticker, run_ts)
            if not path.exists():
                continue
            df = read_parquet(path)
            # Ajout de la colonne run_id (le run_ts source) pour traçabilité
            df = df.with_columns(pl.lit(run_ts).alias("run_id"))
            # Ajout de la colonne product_code si absente
            if "product_code" not in df.columns:
                df = df.with_columns(pl.lit(product_code).alias("product_code"))
            frames.append(df)

    if not frames:
        return pl.DataFrame()

    result = pl.concat(frames, how="diagonal_relaxed")
    logger.info(
        f"Lu {len(frames)} dump(s) brut(s) pour {product_code}: "
        f"{result.height} lignes au total"
    )
    return result


def has_run_today(product_code: str, settings: Settings) -> tuple[bool, str | None]:
    """Vérifie si une historisation a déjà été faite aujourd'hui pour un produit.

    On inspecte les ``run_ts`` (format YYYYMMDDTHHMMSS) de tous les dumps du
    produit : si la partie date (8 premiers caractères) correspond à aujourd'hui,
    c'est qu'un run a déjà été effectué.

    :param product_code: Code produit (ex: "ES").
    :param settings: Configuration.
    :return: Tuple (déjà_fait_aujourd'hui, run_ts_trouvé).
    """
    today_str = datetime.now(UTC).strftime("%Y%m%d")
    tickers = list_tickers(product_code, settings)

    for ticker in tickers:
        for run_ts in list_runs(product_code, ticker, settings):
            if run_ts.startswith(today_str):
                return True, run_ts

    return False, None


def get_latest_run_date(product_code: str, settings: Settings) -> str | None:
    """Retourne la date (YYYYMMDD) du run le plus récent pour un produit.

    :param product_code: Code produit.
    :param settings: Configuration.
    :return: Date du dernier run, ou None si aucun dump n'existe.
    """
    tickers = list_tickers(product_code, settings)
    if not tickers:
        return None

    all_runs: list[str] = []
    for ticker in tickers:
        all_runs.extend(list_runs(product_code, ticker, settings))

    if not all_runs:
        return None

    # Les run_ts sont triés chronologiquement — on prend le dernier
    latest = all_runs[-1]
    return latest[:8]  # YYYYMMDD


def raw_dumps_exist(product_code: str, settings: Settings) -> bool:
    """Vérifie s'il existe au moins un dump brut pour un produit.

    :param product_code: Code produit.
    :param settings: Configuration.
    :return: True si au moins un dump existe.
    """
    return len(list_tickers(product_code, settings)) > 0
