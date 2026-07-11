"""Cache intelligent des contrats futures (1 fichier Parquet par product_code).

Le cache évite d'appeler systématiquement l'endpoint ``/futures/v1/contracts``
à chaque exécution. On stocke les contrats d'un produit dans un fichier
Parquet (``data/cache/contracts/{product_code}.parquet``) accompagné d'un
sidecar ``.meta.json`` qui enregistre ``last_fetched_at``.

**Logique de cache** :
- Si le cache est **frais** (âge < ``ttl_days``) → on lit le Parquet sans
  appeler l'API (log DEBUG "Cache skip").
- Si le cache est **absent** ou **périmé** → on fetch l'API et on réécrit
  le Parquet + sidecar.
- Si ``force_refresh=True`` → on fetch toujours, même si le cache est frais.

Le TTL par défaut est de 30 jours (les contrats changent rarement).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

from massivibe.api.client import MassiveClient
from massivibe.api.contracts import fetch_contracts
from massivibe.config import Settings
from massivibe.logging_setup import get_logger, log_cache_skip
from massivibe.storage.parquet_io import read_meta, read_parquet, write_meta, write_parquet

logger = get_logger("contracts_cache")


class ContractsCache:
    """Cache des contrats d'un produit, stocké en Parquet + sidecar .meta.json.

    Usage typique :

    .. code-block:: python

        cache = ContractsCache("ES", settings)
        df = cache.get(client)  # lit le cache si frais, sinon fetch l'API
    """

    def __init__(self, product_code: str, settings: Settings):
        self.product_code = product_code
        self._settings = settings
        self._parquet_path = settings.contracts_cache_path(product_code)
        self._meta_path = settings.contracts_meta_path(product_code)

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
        """Retourne les contrats du produit, depuis le cache ou l'API.

        :param client: Client Massive authentifié (requis si le cache est
            absent/périmé et qu'il faut fetch l'API).
        :param force_refresh: Si True, ignore le cache et fetch toujours l'API.
        :return: DataFrame Polars des contrats.
        :raises ValueError: Si le cache est absent/périmé et aucun client fourni.
        """
        if not force_refresh and self._is_fresh():
            # Cache frais → on lit le Parquet sans appeler l'API
            meta = read_meta(self._parquet_path)
            last_fetched = meta.get("last_fetched_at", "inconnu") if meta else "inconnu"
            log_cache_skip(logger, "contrats", self.product_code, str(last_fetched))
            return read_parquet(self._parquet_path)

        # Cache absent ou périmé → il faut fetch l'API
        if client is None:
            raise ValueError(
                f"Cache contrats absent/périmé pour {self.product_code} "
                f"et aucun client API fourni pour le rafraîchir"
            )

        logger.info(f"Cache miss/périmé: fetch /contracts pour {self.product_code}")
        df = fetch_contracts(client, self.product_code, self._settings)
        self._write(df, client)
        return df

    def _is_fresh(self) -> bool:
        """Vérifie si le cache est frais (âge < ttl_days).

        :return: True si le Parquet ET le sidecar existent et que l'âge du
            sidecar est inférieur au TTL configuré.
        """
        if not self._parquet_path.exists():
            return False

        meta = read_meta(self._parquet_path)
        if meta is None:
            # Pas de sidecar — on considère le cache comme périmé
            return False

        last_fetched_str = meta.get("last_fetched_at")
        if not last_fetched_str:
            return False

        try:
            # Parse la date ISO (avec timezone)
            last_fetched = datetime.fromisoformat(last_fetched_str)
        except (ValueError, TypeError):
            logger.warning(f"Sidecar invalide pour {self.product_code}: last_fetched_at={last_fetched_str}")
            return False

        # Si la date n'a pas de timezone, on assume UTC
        if last_fetched.tzinfo is None:
            last_fetched = last_fetched.replace(tzinfo=UTC)

        age = datetime.now(UTC) - last_fetched
        is_fresh = age < timedelta(days=self._settings.contracts_ttl_days)

        if not is_fresh:
            logger.debug(
                f"Cache périmé pour {self.product_code}: âge={age.days}j "
                f"(TTL={self._settings.contracts_ttl_days}j)"
            )

        return is_fresh

    def _write(self, df: pl.DataFrame, client: MassiveClient | None = None) -> None:
        """Écrit le DataFrame en Parquet + met à jour le sidecar .meta.json.

        :param df: DataFrame des contrats à écrire.
        :param client: Client utilisé (pour logger la durée du fetch).
        """
        # Métadonnées spécifiques au cache contrats
        extra_meta = {
            "product_code": self.product_code,
            "source_url": f"/futures/v1/contracts?product_code={self.product_code}",
            "last_fetched_at": datetime.now(UTC).isoformat(),
        }

        # On utilise write_parquet qui crée aussi le sidecar avec les champs communs
        # Puis on met à jour le sidecar avec last_fetched_at (champ dynamique)
        write_parquet(df, self._parquet_path, **extra_meta)

        # read_meta pour vérifier que last_fetched_at est bien écrit
        meta = read_meta(self._parquet_path)
        if meta and "last_fetched_at" not in meta:
            # write_parquet écrase tout — on ajoute last_fetched_at manuellement
            meta["last_fetched_at"] = extra_meta["last_fetched_at"]
            write_meta(self._parquet_path, meta)

        logger.info(
            f"Cache contrats mis à jour: {self._parquet_path} ({df.height} contrats)"
        )

    def get_last_fetched(self) -> datetime | None:
        """Retourne la date du dernier fetch du cache, ou None si absent.

        :return: Datetime du dernier fetch (UTC), ou None.
        """
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
