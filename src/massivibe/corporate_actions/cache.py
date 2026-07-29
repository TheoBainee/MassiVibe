"""Cache des corporate actions stocks (1 fichier Parquet par ticker et par kind).

Le cache évite d'appeler systématiquement ``/stocks/v1/splits`` à chaque
exécution. On stocke les splits d'un ticker dans
``cache/corporate_actions/{ticker}/splits.parquet`` accompagné d'un sidecar
``.meta.json`` qui enregistre ``last_fetched_at``.

**Logique de cache** (identique à :class:`massivibe.contracts.cache.ContractsCache`) :
- Si le cache est **frais** (âge < ``instrument_cache_ttl_days``) → lecture du Parquet.
- Si absent/périmé → fetch API + réécriture.
- ``force_refresh=True`` → fetch toujours.

Le TTL est commun à tous les caches d'instruments (contrats futures, corporate
actions stocks) via ``[instrument_cache] ttl_days``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

from massivibe.api.client import MassiveClient
from massivibe.api.corporate_actions import fetch_dividends, fetch_splits
from massivibe.config import Settings
from massivibe.logging_setup import get_logger, log_cache_skip
from massivibe.storage.parquet_io import read_meta, read_parquet, write_parquet

logger = get_logger("corp_actions_cache")


class CorporateActionsCache:
    """Cache des corporate actions d'un ticker stock, stocké en Parquet + sidecar.

    Usage typique :

    .. code-block:: python

        cache = CorporateActionsCache("AAPL", "splits", settings)
        df = cache.get(client)  # lit le cache si frais, sinon fetch l'API
    """

    def __init__(self, ticker: str, kind: str, settings: Settings):
        """Initialise le cache.

        :param ticker: Symbole nu du stock (ex: "AAPL").
        :param kind: Type d'action : "splits" ou "dividends" (les deux sont supportés).
        :param settings: Configuration (pour cache_dir, instrument_cache_ttl_days).
        """
        self.ticker = ticker
        self.kind = kind
        self._settings = settings
        self._parquet_path = settings.corporate_actions_path(ticker, kind)
        self._meta_path = settings.corporate_actions_meta_path(ticker, kind)

    @property
    def parquet_path(self) -> Path:
        return self._parquet_path

    @property
    def meta_path(self) -> Path:
        return self._meta_path

    @property
    def exists(self) -> bool:
        return self._parquet_path.exists()

    def get(
        self,
        client: MassiveClient | None = None,
        force_refresh: bool = False,
    ) -> pl.DataFrame:
        """Retourne les corporate actions du ticker, depuis le cache ou l'API.

        :param client: Client Massive (requis si le cache est absent/périmé).
        :param force_refresh: Si True, ignore le cache et fetch toujours l'API.
        :raises ValueError: Si le cache est absent/périmé et aucun client fourni.
        """
        if not force_refresh and self._is_fresh():
            meta = read_meta(self._parquet_path)
            last_fetched = meta.get("last_fetched_at", "inconnu") if meta else "inconnu"
            log_cache_skip(logger, f"corp_actions.{self.kind}", self.ticker, str(last_fetched))
            return read_parquet(self._parquet_path)

        if client is None:
            raise ValueError(
                f"Cache {self.kind} absent/périmé pour {self.ticker} "
                f"et aucun client API fourni pour le rafraîchir"
            )

        logger.info(f"Cache miss/périmé: fetch /stocks/v1/{self.kind} pour {self.ticker}")
        if self.kind == "splits":
            df = fetch_splits(client, self.ticker, self._settings)
        elif self.kind == "dividends":
            df = fetch_dividends(client, self.ticker, self._settings)
        else:
            raise NotImplementedError(f"Kind '{self.kind}' non implémenté")
        self._write(df)
        return df

    def _is_fresh(self) -> bool:
        """Vérifie si le cache est frais (âge < instrument_cache_ttl_days)."""
        if not self._parquet_path.exists():
            return False
        meta = read_meta(self._parquet_path)
        if meta is None:
            return False
        last_fetched_str = meta.get("last_fetched_at")
        if not last_fetched_str:
            return False
        try:
            last_fetched = datetime.fromisoformat(last_fetched_str)
        except (ValueError, TypeError):
            logger.warning(f"Sidecar invalide pour {self.ticker}/{self.kind}")
            return False
        if last_fetched.tzinfo is None:
            last_fetched = last_fetched.replace(tzinfo=UTC)
        age = datetime.now(UTC) - last_fetched
        is_fresh = age < timedelta(days=self._settings.instrument_cache_ttl_days)
        if not is_fresh:
            logger.debug(
                f"Cache {self.kind} périmé pour {self.ticker}: âge={age.days}j "
                f"(TTL={self._settings.instrument_cache_ttl_days}j)"
            )
        return is_fresh

    def _write(self, df: pl.DataFrame) -> None:
        """Écrit le DataFrame en Parquet + met à jour le sidecar .meta.json."""
        extra_meta = {
            "ticker": self.ticker,
            "kind": self.kind,
            "source_url": f"/stocks/v1/{self.kind}?ticker={self.ticker}",
            "last_fetched_at": datetime.now(UTC).isoformat(),
        }
        write_parquet(df, self._parquet_path, **extra_meta)
        # S'assurer que last_fetched_at est bien présent (write_parquet fusionne)
        meta = read_meta(self._parquet_path)
        if meta and "last_fetched_at" not in meta:
            meta["last_fetched_at"] = extra_meta["last_fetched_at"]
            from massivibe.storage.parquet_io import write_meta

            write_meta(self._parquet_path, meta)
        logger.info(f"Cache {self.kind} mis à jour: {self._parquet_path} ({df.height} lignes)")

    def get_last_fetched(self) -> datetime | None:
        """Retourne la date du dernier fetch, ou None si absent."""
        meta = read_meta(self._parquet_path)
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
