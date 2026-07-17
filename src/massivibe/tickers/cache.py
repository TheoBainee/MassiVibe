"""Caches Parquet des tickers de référence (all-tickers + ticker-types).

Deux caches indépendants, stockés dans ``data/cache/tickers/`` :

- ``all_tickers.parquet`` + ``all_tickers.meta.json`` : snapshot de
  ``/v3/reference/tickers``. Le filtre de marché appliqué au fetch
  (``"stocks"`` par défaut, ou ``None`` pour tous les marchés) est conservé
  dans le sidecar (champ ``market_filter``) pour qu'un ``search`` sache quel
  périmètre le cache couvre.
- ``ticker_types.parquet`` + ``ticker_types.meta.json`` : catalogue des types
  de tickers (``/v3/reference/tickers/types``).

**Logique de cache** (identique à :class:`massivibe.contracts.cache.ContractsCache`) :

- Si le cache est **frais** (âge < ``instrument_cache_ttl_days``) → on lit le
  Parquet sans appeler l'API (log DEBUG "Cache skip").
- Si le cache est **absent** ou **périmé** → on fetch l'API et on réécrit le
  Parquet + sidecar.
- Si ``force_refresh=True`` → on fetch toujours, même si le cache est frais.

**Filtre de marché et fraîcheur** : pour ``AllTickersCache``, on compare le
``market_filter`` demandé à celui enregistré dans le sidecar. Si un cache
existe pour ``market="stocks"`` mais qu'on demande ``market=None``, le cache
n'est **pas** considéré comme frais (le périmètre diffère) → on re-fetch. Cela
évite de servir un cache stocks alors qu'on veut rechercher sur tous les
marchés.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

from massivibe.api.client import MassiveClient
from massivibe.api.tickers import fetch_all_tickers, fetch_ticker_types
from massivibe.config import Settings
from massivibe.logging_setup import get_logger, log_cache_skip
from massivibe.storage.parquet_io import read_meta, read_parquet, write_meta, write_parquet

logger = get_logger("tickers_cache")


def _is_fresh_by_meta(parquet_path: Path, ttl_days: int) -> bool:
    """Vérifie la fraîcheur d'un cache à partir de son sidecar (âge < ttl_days)."""
    if not parquet_path.exists():
        return False

    meta = read_meta(parquet_path)
    if meta is None:
        return False

    last_fetched_str = meta.get("last_fetched_at")
    if not last_fetched_str:
        return False

    try:
        last_fetched = datetime.fromisoformat(last_fetched_str)
    except (ValueError, TypeError):
        logger.warning(f"Sidecar invalide pour {parquet_path.name}: last_fetched_at={last_fetched_str}")
        return False

    if last_fetched.tzinfo is None:
        last_fetched = last_fetched.replace(tzinfo=UTC)

    age = datetime.now(UTC) - last_fetched
    is_fresh = age < timedelta(days=ttl_days)
    if not is_fresh:
        logger.debug(f"Cache périmé pour {parquet_path.name}: âge={age.days}j (TTL={ttl_days}j)")
    return is_fresh


def _last_fetched_from_meta(parquet_path: Path) -> datetime | None:
    """Retourne le datetime du dernier fetch lu dans le sidecar, ou None."""
    meta = read_meta(parquet_path)
    if meta is None:
        return None
    last_fetched_str = meta.get("last_fetched_at")
    if not last_fetched_str:
        return None
    try:
        dt = datetime.fromisoformat(last_fetched_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except (ValueError, TypeError):
        return None


class AllTickersCache:
    """Cache du snapshot all-tickers, stocké en Parquet + sidecar .meta.json.

    Usage typique :

    .. code-block:: python

        cache = AllTickersCache(settings, market="stocks")
        df = cache.get(client)  # lit le cache si frais, sinon fetch l'API
    """

    def __init__(self, settings: Settings, market: str | None = "stocks"):
        """:param settings: Configuration (pour chemins + TTL).
        :param market: Filtre de marché du cache (``"stocks"`` par défaut, ou
            ``None`` pour tous les marchés). Conservé dans le sidecar et comparé
            à la fraîcheur pour éviter de servir un cache d'un périmètre différent.
        """
        self.market_filter = market
        self._settings = settings
        self._parquet_path = settings.all_tickers_cache_path()
        self._meta_path = settings.all_tickers_meta_path()

    @property
    def parquet_path(self) -> Path:
        """Chemin du fichier Parquet du cache."""
        return self._parquet_path

    @property
    def meta_path(self) -> Path:
        """Chemin du sidecar .meta.json du cache."""
        return self._meta_path

    @property
    def exists(self) -> bool:
        """True si le fichier Parquet du cache existe sur disque."""
        return self._parquet_path.exists()

    def get(
        self,
        client: MassiveClient | None = None,
        force_refresh: bool = False,
    ) -> pl.DataFrame:
        """Retourne le snapshot all-tickers, depuis le cache ou l'API.

        :param client: Client Massive (requis si le cache est absent/périmé et
            qu'il faut fetch l'API).
        :param force_refresh: Si True, ignore le cache et fetch toujours l'API.
        :return: DataFrame Polars des tickers.
        :raises ValueError: Si le cache est absent/périmé et aucun client fourni.
        """
        if not force_refresh and self._is_fresh():
            meta = read_meta(self._parquet_path)
            last_fetched = meta.get("last_fetched_at", "inconnu") if meta else "inconnu"
            market_desc = self.market_filter if self.market_filter is not None else "tous"
            log_cache_skip(logger, "tickers", f"market={market_desc}", str(last_fetched))
            return read_parquet(self._parquet_path)

        if client is None:
            raise ValueError(
                f"Cache all-tickers absent/périmé (market={self.market_filter}) "
                f"et aucun client API fourni pour le rafraîchir"
            )

        market_desc = self.market_filter if self.market_filter is not None else "tous"
        logger.info(f"Cache miss/périmé: fetch /v3/reference/tickers (market={market_desc})")
        df = fetch_all_tickers(client, self._settings, market=self.market_filter)
        self._write(df)
        return df

    def _is_fresh(self) -> bool:
        """Vérifie la fraîcheur (âge < TTL) **et** la cohérence du filtre de marché.

        Un cache ``market="stocks"`` n'est pas frais si on demande ``market=None``
        (et inversement) — les périmètres diffèrent.
        """
        is_fresh = _is_fresh_by_meta(self._parquet_path, self._settings.instrument_cache_ttl_days)
        if not is_fresh:
            return False

        # Cohérence du market_filter avec le sidecar
        meta = read_meta(self._parquet_path)
        if meta is None:
            return False
        cached_market = meta.get("market_filter", "__missing__")
        # On normalise None vs "stocks" : on compare en stringifiant (None -> "tous").
        want = self.market_filter if self.market_filter is not None else "tous"
        have = cached_market if cached_market != "__missing__" else "tous"
        if want != have:
            logger.debug(
                f"Cache all-tickers existant mais market_filter diffère "
                f"(want={want}, have={have}) → re-fetch nécessaire"
            )
            return False
        return True

    def _write(self, df: pl.DataFrame) -> None:
        """Écrit le DataFrame en Parquet + met à jour le sidecar .meta.json."""
        market_desc = self.market_filter if self.market_filter is not None else "tous"
        extra_meta = {
            "market_filter": market_desc,
            "source_url": f"{_SOURCE_ALL_TICKERS}?market={market_desc}",
            "last_fetched_at": datetime.now(UTC).isoformat(),
        }
        write_parquet(df, self._parquet_path, **extra_meta)

        # write_parquet écrase le sidecar avec les champs communs + extra_meta ;
        # on s'assure que last_fetched_at est bien présent (cohérence avec ContractsCache).
        meta = read_meta(self._parquet_path)
        if meta and "last_fetched_at" not in meta:
            meta["last_fetched_at"] = extra_meta["last_fetched_at"]
            write_meta(self._parquet_path, meta)

        logger.info(
            f"Cache all-tickers mis à jour: {self._parquet_path} "
            f"({df.height} tickers, market={market_desc})"
        )

    def get_last_fetched(self) -> datetime | None:
        """Retourne la date du dernier fetch du cache, ou None si absent."""
        return _last_fetched_from_meta(self._parquet_path)


class TickerTypesCache:
    """Cache du catalogue ticker-types, stocké en Parquet + sidecar .meta.json."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._parquet_path = settings.ticker_types_cache_path()
        self._meta_path = settings.ticker_types_meta_path()

    @property
    def parquet_path(self) -> Path:
        """Chemin du fichier Parquet du cache."""
        return self._parquet_path

    @property
    def meta_path(self) -> Path:
        """Chemin du sidecar .meta.json du cache."""
        return self._meta_path

    @property
    def exists(self) -> bool:
        """True si le fichier Parquet du cache existe sur disque."""
        return self._parquet_path.exists()

    def get(
        self,
        client: MassiveClient | None = None,
        force_refresh: bool = False,
    ) -> pl.DataFrame:
        """Retourne le catalogue ticker-types, depuis le cache ou l'API.

        :param client: Client Massive (requis si le cache est absent/périmé).
        :param force_refresh: Si True, ignore le cache et fetch toujours l'API.
        :return: DataFrame Polars des types de tickers.
        :raises ValueError: Si le cache est absent/périmé et aucun client fourni.
        """
        if not force_refresh and self._is_fresh():
            meta = read_meta(self._parquet_path)
            last_fetched = meta.get("last_fetched_at", "inconnu") if meta else "inconnu"
            log_cache_skip(logger, "ticker_types", "all", str(last_fetched))
            return read_parquet(self._parquet_path)

        if client is None:
            raise ValueError(
                "Cache ticker-types absent/périmé et aucun client API fourni pour le rafraîchir"
            )

        logger.info("Cache miss/périmé: fetch /v3/reference/tickers/types")
        df = fetch_ticker_types(client, self._settings)
        self._write(df)
        return df

    def _is_fresh(self) -> bool:
        return _is_fresh_by_meta(self._parquet_path, self._settings.instrument_cache_ttl_days)

    def _write(self, df: pl.DataFrame) -> None:
        extra_meta = {
            "source_url": _SOURCE_TICKER_TYPES,
            "last_fetched_at": datetime.now(UTC).isoformat(),
        }
        write_parquet(df, self._parquet_path, **extra_meta)

        meta = read_meta(self._parquet_path)
        if meta and "last_fetched_at" not in meta:
            write_meta(self._parquet_path, meta)

        logger.info(
            f"Cache ticker-types mis à jour: {self._parquet_path} ({df.height} types)"
        )

    def get_last_fetched(self) -> datetime | None:
        """Retourne la date du dernier fetch du cache, ou None si absent."""
        return _last_fetched_from_meta(self._parquet_path)


# Constantes pour les source_url du sidecar (évite d'importer les paths privés)
_SOURCE_ALL_TICKERS = "/v3/reference/tickers"
_SOURCE_TICKER_TYPES = "/v3/reference/tickers/types"
