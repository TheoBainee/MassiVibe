"""Utilitaires de lecture/écriture Parquet avec sidecar .meta.json systématique.

**Principe du sidecar** : à chaque fichier Parquet écrit par MyQuantStore, on
crée un fichier JSON annexe (même nom de base, extension ``.meta.json``)
qui stocke les métadonnées de gestion du fichier, séparément des données métier.

```
data/cache/contracts/ES.parquet       # données tabulaires
data/cache/contracts/ES.meta.json     # métadonnées (sidecar)
```

**Pourquoi séparé du Parquet ?** Stocker des métadonnées (last_fetched_at,
source_url, etc.) dans le Parquet polluerait les données métier. Un fichier
JSON séparé est plus simple à lire/écrire et ne parasite pas les
``read_parquet`` métier.

Le sidecar contient toujours des champs communs (schema_version, created_at,
row_count, columns, dtypes, file_size_bytes) plus des champs spécifiques
passés via ``**extra_meta``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import polars as pl

from myquantstore.logging_setup import get_logger

logger = get_logger("storage")

# Version du schéma de métadonnées — incrémenter si la structure du sidecar change
SCHEMA_VERSION = "1.0"


def write_parquet(df: pl.DataFrame, path: str | Path, **extra_meta: Any) -> Path:
    """Écrit un DataFrame Polars en Parquet + son sidecar .meta.json.

    :param df: DataFrame à écrire.
    :param path: Chemin du fichier Parquet (ex: "data/raw/ES/ESM5/20260711T183000.parquet").
    :param extra_meta: Métadonnées additionnelles spécifiques au type de fichier
        (ex: product_code, ticker, run_ts, source_url, last_fetched_at…).
    :return: Le chemin du fichier Parquet écrit.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Écriture du Parquet
    df.write_parquet(path)
    file_size = path.stat().st_size

    # Construction du sidecar .meta.json
    meta: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "row_count": df.height,
        "columns": df.columns,
        "dtypes": {col: str(dtype) for col, dtype in zip(df.columns, df.dtypes, strict=False)},
        "file_size_bytes": file_size,
    }
    # Fusion avec les métadonnées spécifiques
    meta.update(extra_meta)

    # Écriture du sidecar (même nom de base, extension .meta.json)
    meta_path = _meta_path_for(path)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False, default=str)

    logger.debug(
        f"Écrit {path.name} ({df.height} lignes, {file_size} bytes) + sidecar {meta_path.name}"
    )
    return path


def read_parquet(path: str | Path) -> pl.DataFrame:
    """Lit un fichier Parquet.

    :param path: Chemin du fichier Parquet.
    :return: DataFrame Polars.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Fichier Parquet introuvable : {path}")
    return pl.read_parquet(path)


def read_meta(parquet_path: str | Path) -> dict[str, Any] | None:
    """Lit le sidecar .meta.json associé à un fichier Parquet.

    :param parquet_path: Chemin du fichier Parquet.
    :return: Dictionnaire des métadonnées, ou None si le sidecar n'existe pas.
    """
    meta_path = _meta_path_for(parquet_path)
    if not meta_path.exists():
        return None
    with open(meta_path, encoding="utf-8") as f:
        return cast("dict[str, Any]", json.load(f))


def write_meta(parquet_path: str | Path, meta: dict[str, Any]) -> Path:
    """Écrit (ou écrase) uniquement le sidecar .meta.json d'un fichier Parquet.

    Utile pour mettre à jour les métadonnées sans réécrire le Parquet
    (ex: rafraîchir ``last_fetched_at`` sans re-télécharger les données).

    :param parquet_path: Chemin du fichier Parquet.
    :param meta: Métadonnées à écrire.
    :return: Le chemin du sidecar écrit.
    """
    meta_path = _meta_path_for(parquet_path)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False, default=str)
    return meta_path


def _meta_path_for(parquet_path: str | Path) -> Path:
    """Calcule le chemin du sidecar .meta.json pour un fichier Parquet donné.

    Ex: "data/raw/ES/ESM5/20260711T183000.parquet"
     -> "data/raw/ES/ESM5/20260711T183000.meta.json"
    """
    path = Path(parquet_path)
    return path.with_suffix(".meta.json")
