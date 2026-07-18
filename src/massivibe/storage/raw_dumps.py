"""Gestion des dumps pseudo-bruts (1 fichier Parquet par run).

Les "dumps bruts" (ou pseudo-bruts) ne sont **pas** la réponse JSON brute de l'API.
Ce sont les données retournées par l'API après normalisation minimale au format
interne canonique de MassiVibe :
- conversion des timestamps (ns/ms → Datetime[ns] UTC)
- normalisation/renommage des champs
- ajout des colonnes d'identité (symbol, instrument_type, product_code, run_id)
- casts (volume/transactions → Int32, etc.)

Cette pratique est choisie pour praticité et performance.

**Contrainte absolue (même en alpha)** : il doit toujours être possible de
reconstruire l'agrégat complet à partir de ces dumps (read_all_runs + concat
+ dédup keep=last sur (window_start, ticker) + casts).

Structure (layout multi-type) ::

    data/raw/
    ├─ futures/                 # {type}
    │  ├─ ES/                   # {symbol}  (produit futures)
    │  │  ├─ ESM5/              # {ticker}  (contrat individuel)
    │  │  │  ├─ 20260704T180000.parquet
    │  │  │  └─ 20260704T180000.meta.json
    │  │  ├─ ESU5/
    │  │  └─ ...
    │  └─ NQ/
    │     └─ ...
    ├─ stocks/
    │  └─ AAPL/                 # {symbol}
    │     └─ AAPL/              # {ticker} = symbole (pas de sous-niveau contrat)
    │        └─ 20260711T183000.parquet
    └─ ...

Chaque fichier est **immuable** (jamais écrasé) — un nouveau run crée un
nouveau fichier avec un ``run_ts`` unique. Cela permet l'audit et la
re-agrégation à partir des dumps pseudo-bruts.

Pour les types à symbole unique (forex, stocks, indices), le niveau ``ticker``
est identique au ``symbol`` (pas de notion de contrat individuel).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from massivibe.config import Settings
from massivibe.instruments import Instrument
from massivibe.logging_setup import get_logger
from massivibe.storage.parquet_io import read_parquet, write_parquet

logger = get_logger("raw_dumps")


def save_raw_dump(
    df: pl.DataFrame,
    instrument: Instrument,
    ticker: str,
    run_ts: str,
    settings: Settings,
    source_url: str = "",
    page_count: int = 0,
) -> Path:
    """Sauvegarde un dump pseudo-brut pour un run donné.

    Le DataFrame reçu est déjà normalisé au format canonique interne.
    Le fichier est écrit dans ``data/raw/{type}/{symbol}/{ticker}/{run_ts}.parquet``
    avec son sidecar ``.meta.json``.

    :param df: DataFrame des chandeliers OHLCV (normalisé) à sauvegarder.
    :param instrument: Instrument cible (porte le type et le symbole).
    :param ticker: Ticker de trading (contrat futures, ou symbole pour les autres).
    :param run_ts: Identifiant du run (format YYYYMMDDTHHMMSS).
    :param settings: Configuration.
    :param source_url: URL source de l'appel API (pour audit).
    :param page_count: Nombre de pages paginées pour ce dump.
    :return: Le chemin du fichier Parquet écrit.
    """
    path = settings.raw_dump_path(instrument, ticker, run_ts)

    extra_meta: dict[str, object] = {
        "instrument_type": instrument.type.value,
        "symbol": instrument.symbol,
        "ticker": ticker,
        "run_ts": run_ts,
        "source_url": source_url,
        "page_count": page_count,
    }

    # window_start_min / max pour audit rapide (si la colonne existe)
    if "window_start" in df.columns and df.height > 0:
        ws_min = df["window_start"].min()
        ws_max = df["window_start"].max()
        if ws_min is not None:
            extra_meta["window_start_min"] = str(ws_min)
        if ws_max is not None:
            extra_meta["window_start_max"] = str(ws_max)

    write_parquet(df, path, **extra_meta)
    logger.info(f"Dump brut sauvegardé: {path} ({df.height} chandeliers)")
    return path


def list_runs(instrument: Instrument, ticker: str, settings: Settings) -> list[str]:
    """Liste tous les ``run_ts`` disponibles pour un ticker donné.

    :return: Liste triée des run_ts (YYYYMMDDTHHMMSS) par ordre chronologique.
    """
    ticker_dir = settings.raw_dumps_dir() / instrument.path_segment / instrument.symbol / ticker
    if not ticker_dir.exists():
        return []
    return sorted(f.stem for f in ticker_dir.glob("*.parquet") if f.suffix == ".parquet")


def list_tickers(instrument: Instrument, settings: Settings) -> list[str]:
    """Liste tous les tickers ayant au moins un dump brut pour un instrument.

    Pour futures : les contrats individuels (ex: ["ESH5", "ESM5", "ESU5"]).
    Pour les autres types : le symbole unique (ex: ["AAPL"]).
    """
    symbol_dir = settings.raw_dumps_dir() / instrument.path_segment / instrument.symbol
    if not symbol_dir.exists():
        return []
    return sorted(d.name for d in symbol_dir.iterdir() if d.is_dir())


def read_all_runs(instrument: Instrument, settings: Settings) -> pl.DataFrame:
    """Lit et concatène tous les dumps pseudo-bruts d'un instrument (tous tickers, tous runs).

    Utilisé par l'agrégateur pour reconstruire l'historique complet à partir
    des dumps pseudo-bruts. Les dumps sont lus par ordre chronologique des ``run_ts``
    pour que la déduplication ``keep="last"`` conserve les données les plus récentes.

    :return: DataFrame Polars concaténé avec colonne ``run_id`` (le run_ts source).
    """
    tickers = list_tickers(instrument, settings)
    if not tickers:
        logger.warning(f"Aucun dump pseudo-brut trouvé pour {instrument.key}")
        return pl.DataFrame()

    frames: list[pl.DataFrame] = []

    for ticker in tickers:
        run_ts_list = list_runs(instrument, ticker, settings)
        for run_ts in run_ts_list:
            path = settings.raw_dump_path(instrument, ticker, run_ts)
            if not path.exists():
                continue
            df = read_parquet(path)
            df = df.with_columns(pl.lit(run_ts).alias("run_id"))
            # Assurer la présence des colonnes identité
            if "symbol" not in df.columns:
                df = df.with_columns(pl.lit(instrument.symbol).alias("symbol"))
            if "instrument_type" not in df.columns:
                df = df.with_columns(pl.lit(instrument.type.value).alias("instrument_type"))
            if "product_code" not in df.columns:
                df = df.with_columns(pl.lit(instrument.symbol).alias("product_code"))
            frames.append(df)

    if not frames:
        return pl.DataFrame()

    result = pl.concat(frames, how="diagonal_relaxed")
    logger.info(
        f"Lu {len(frames)} dump(s) pseudo-brut(s) pour {instrument.key}: "
        f"{result.height} lignes au total"
    )
    return result


def has_run_today(instrument: Instrument, settings: Settings) -> tuple[bool, str | None]:
    """Vérifie si une historisation a déjà été faite aujourd'hui pour un instrument.

    On inspecte les ``run_ts`` (YYYYMMDDTHHMMSS) de tous les dumps : si la
    partie date (8 premiers caractères) correspond à aujourd'hui, un run a
    déjà été effectué.

    :return: Tuple (déjà_fait_aujourd'hui, run_ts_trouvé).
    """
    today_str = datetime.now(UTC).strftime("%Y%m%d")
    tickers = list_tickers(instrument, settings)

    for ticker in tickers:
        for run_ts in list_runs(instrument, ticker, settings):
            if run_ts.startswith(today_str):
                return True, run_ts

    return False, None


def get_latest_run_date(instrument: Instrument, settings: Settings) -> str | None:
    """Retourne la date (YYYYMMDD) du run le plus récent pour un instrument."""
    tickers = list_tickers(instrument, settings)
    if not tickers:
        return None

    all_runs: list[str] = []
    for ticker in tickers:
        all_runs.extend(list_runs(instrument, ticker, settings))

    if not all_runs:
        return None

    latest = all_runs[-1]
    return latest[:8]


def raw_dumps_exist(instrument: Instrument, settings: Settings) -> bool:
    """Vérifie s'il existe au moins un dump pseudo-brut pour un instrument."""
    return len(list_tickers(instrument, settings)) > 0
