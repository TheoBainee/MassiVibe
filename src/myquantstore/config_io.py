"""Lecture/écriture du fichier config.toml (instruments).

Utilise ``tomlkit`` pour préserver commentaires et formatage.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import tomlkit
from tomlkit.items import Array, Table

from myquantstore.config import (
    Settings,
    get_repo_config_path,
    get_user_config_path,
)
from myquantstore.instruments import InstrumentType
from myquantstore.logging_setup import get_logger

logger = get_logger("config_io")

# Clé TOML [instruments] par InstrumentType
_TYPE_TO_KEY: dict[InstrumentType, str] = {
    InstrumentType.FUTURES: "futures",
    InstrumentType.FOREX: "forex",
    InstrumentType.STOCKS: "stocks",
    InstrumentType.INDICES: "indices",
    InstrumentType.OPTIONS: "options",
}


def resolve_writable_config_path() -> Path:
    """Chemin du config.toml à modifier (XDG s'il existe, sinon repo, sinon XDG à créer)."""
    user = get_user_config_path()
    if user.exists():
        return user
    repo = get_repo_config_path()
    if repo.exists():
        return repo
    # Par défaut on ciblera le path utilisateur (sera créé)
    return user


def load_toml_document(path: Path) -> Any:
    """Charge un document tomlkit depuis ``path``."""
    text = path.read_text(encoding="utf-8")
    return tomlkit.parse(text)


def add_instruments_to_config(
    path: Path,
    items: list[tuple[InstrumentType, str]],
    *,
    dry_run: bool = False,
) -> dict[str, list[str]]:
    """Ajoute des symboles dans ``[instruments]`` selon leur type.

    :param path: Chemin du ``config.toml``.
    :param items: Liste ``(InstrumentType, symbole_nu)``.
    :param dry_run: Si True, ne écrit pas le fichier.
    :return: Dict ``{type_key: [symboles réellement ajoutés]}``.
    :raises FileNotFoundError: Si le fichier n'existe pas.
    :raises ValueError: Si la section ``[instruments]`` est absente.
    """
    if not path.exists():
        raise FileNotFoundError(f"config.toml introuvable : {path}")

    doc = load_toml_document(path)
    if "instruments" not in doc:
        raise ValueError(f"Section [instruments] absente dans {path}")

    instruments = doc["instruments"]
    if not isinstance(instruments, Table):
        raise ValueError("[instruments] n'est pas une table TOML valide")

    added: dict[str, list[str]] = {k: [] for k in _TYPE_TO_KEY.values()}
    skipped: list[str] = []

    for inst_type, symbol in items:
        key = _TYPE_TO_KEY[inst_type]
        current = _get_symbol_list(instruments, key)
        if symbol in current:
            skipped.append(f"{key}:{symbol}")
            continue
        current.append(symbol)
        _set_symbol_list(instruments, key, current)
        added[key].append(symbol)

    if dry_run:
        logger.info(f"[dry-run] Ajouts prévus: { {k: v for k, v in added.items() if v} }")
        return added

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    logger.info(
        f"Config mise à jour {path}: ajoutés={ {k: v for k, v in added.items() if v} }, "
        f"déjà présents={skipped}"
    )
    return added


def _get_symbol_list(instruments: Table, key: str) -> list[str]:
    """Retourne la liste Python des symboles pour une clé."""
    if key not in instruments:
        return []
    val = instruments[key]
    if isinstance(val, Array):
        return [str(x) for x in val]
    if isinstance(val, list):
        return [str(x) for x in val]
    return []


def _set_symbol_list(instruments: Table, key: str, symbols: list[str]) -> None:
    """Écrit une Array tomlkit pour la clé (style inline compact)."""
    arr: Array = tomlkit.array()
    arr.multiline(False)
    for s in symbols:
        arr.append(s)
    instruments[key] = arr


def symbols_already_configured(settings: Settings, inst_type: InstrumentType, symbol: str) -> bool:
    """True si le symbole est déjà dans la conf pour ce type."""
    mapping = {
        InstrumentType.FUTURES: settings.futures,
        InstrumentType.FOREX: settings.forex,
        InstrumentType.STOCKS: settings.stocks,
        InstrumentType.INDICES: settings.indices,
        InstrumentType.OPTIONS: settings.options,
    }
    return symbol in mapping[inst_type]
