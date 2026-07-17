"""Recherche locale d'instruments dans le cache all-tickers + ajout à la config.

Ce module implémente la logique métier de la commande ``massivibe tickers search`` :

- :func:`search_tickers` : filtre le DataFrame du cache all-tickers selon
  plusieurs critères (ticker, marché, type, statut actif, texte libre dans le
  nom, exchange). C'est une opération **purement locale** — aucun appel API.
- :func:`add_to_config` : ajoute les tickers sélectionnés à la section
  ``[instruments]`` du ``config.toml`` utilisateur, en déduisant la section
  cible (``stocks``, ``forex``, ``indices``, ``crypto``…) depuis le champ
  ``type`` du ticker résolu via le cache ``ticker_types``. Écrit avec backup
  ``.bak`` et déduplication.

**Mapping type → section [instruments]** : le champ ``type`` renvoyé par
``/v3/reference/tickers`` est un *code* (ex: ``"CS"`` = Common Stock,
``"ETF"`` = Exchange Traded Fund). On ne peut pas mapper directement ce code
vers une section ``[instruments]`` — il faut passer par le catalogue
``ticker_types`` qui associe chaque code à un ``asset_class``
(``stocks``/``options``/``crypto``/``fx``/``indices``), puis mapper
``asset_class`` vers :class:`InstrumentType`.

NB : seuls ``futures`` et ``stocks`` sont implémentés dans MassiVibe
(cf. ``InstrumentType.implemented``). Pour les autres asset classes
(forex/indices/options/crypto), on n'ajoute pas les tickers à la config
(ils ne pourraient pas être fetchés) — on l'avertit et on les ignore.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl

from massivibe.config import Settings, get_user_config_path
from massivibe.instruments import InstrumentType
from massivibe.logging_setup import get_logger

logger = get_logger("tickers_search")


# Mapping asset_class (champ ticker_types) -> InstrumentType.
# "crypto" n'a pas d'InstrumentType dédié (non géré par MassiVibe) -> None.
_ASSET_CLASS_TO_INSTRUMENT_TYPE: dict[str, InstrumentType | None] = {
    "stocks": InstrumentType.STOCKS,
    "fx": InstrumentType.FOREX,
    "indices": InstrumentType.INDICES,
    "options": InstrumentType.OPTIONS,
    "crypto": None,  # non géré par MassiVibe
}


def search_tickers(
    df: pl.DataFrame,
    *,
    ticker: str | None = None,
    market: str | None = None,
    type_code: str | None = None,
    active: bool | None = None,
    name_contains: str | None = None,
    exchange: str | None = None,
    limit: int | None = None,
) -> pl.DataFrame:
    """Filtre le DataFrame du cache all-tickers selon les critères donnés.

    Tous les critères sont optionnels et combinés par ET (AND). Les filtres
    texte (``ticker``, ``name_contains``, ``exchange``, ``type_code``,
    ``market``) sont insensibles à la casse et correspondent à un *sous-chaîne*
    (``contains``), sauf ``ticker`` qui accepte aussi une liste séparée par des
    virgules pour une recherche exacte multi-symboles.

    :param df: DataFrame du cache all-tickers (une ligne par ticker).
    :param ticker: Symbole exact ou sous-chaîne. Si contient une virgule, traité
        comme une liste de symboles exacts (ex: ``"AAPL,MSFT"``).
    :param market: Filtre sur le champ ``market`` (stocks/crypto/fx/otc/indices).
    :param type_code: Filtre sur le champ ``type`` (code: CS, ETF, ETN…).
    :param active: ``True`` = uniquement actifs, ``False`` = délistés, ``None`` = les deux.
    :param name_contains: Sous-chaîne recherchée dans le champ ``name`` (insensible à la casse).
    :param exchange: Sous-chaîne recherchée dans le champ ``primary_exchange`` (insensible à la casse).
    :param limit: Nombre max de résultats (après tri par ticker ascendant).
    :return: DataFrame filtré (nouvelle instance), trié par ``ticker``.
    """
    if df.is_empty():
        return df

    out = df

    # --- ticker : liste exacte OU sous-chaîne ---
    if ticker:
        cleaned = ticker.strip()
        if "," in cleaned:
            # Liste de symboles exacts (insensible à la casse)
            symbols = [s.strip().upper() for s in cleaned.split(",") if s.strip()]
            if "ticker" in out.columns:
                out = out.filter(pl.col("ticker").str.to_uppercase().is_in(symbols))
        elif "ticker" in out.columns:
            out = out.filter(pl.col("ticker").str.to_uppercase().str.contains(cleaned.upper()))

    # --- market : correspondance exacte insensible à la casse ---
    if market and "market" in out.columns:
        out = out.filter(pl.col("market").str.to_uppercase() == market.upper())

    # --- type : sous-chaîne insensible à la casse ---
    if type_code and "type" in out.columns:
        out = out.filter(pl.col("type").str.to_uppercase().str.contains(type_code.upper()))

    # --- active : booléen exact ---
    if active is not None and "active" in out.columns:
        out = out.filter(pl.col("active") == active)

    # --- name : sous-chaîne insensible à la casse ---
    if name_contains and "name" in out.columns:
        out = out.filter(pl.col("name").str.to_lowercase().str.contains(name_contains.lower()))

    # --- exchange : sous-chaîne insensible à la casse ---
    if exchange and "primary_exchange" in out.columns:
        out = out.filter(
            pl.col("primary_exchange").str.to_lowercase().str.contains(exchange.lower())
        )

    # Tri déterministe par ticker
    if "ticker" in out.columns:
        out = out.sort("ticker")

    # Limite (après tri)
    if limit is not None and limit > 0:
        out = out.head(limit)

    return out


def build_type_to_asset_class_map(types_df: pl.DataFrame) -> dict[str, str]:
    """Construit le mapping ``code -> asset_class`` depuis le cache ticker_types.

    :param types_df: DataFrame du cache ticker_types (colonnes ``code``,
        ``asset_class`` au minimum).
    :return: Dict ``{"CS": "stocks", "ETF": "stocks", "CURRENCY": "fx", ...}``.
    """
    mapping: dict[str, str] = {}
    if types_df.is_empty() or "code" not in types_df.columns:
        return mapping
    for row in types_df.select("code", "asset_class").iter_rows(named=True):
        code = row.get("code")
        asset = row.get("asset_class")
        if code is not None and asset is not None:
            mapping[str(code)] = str(asset)
    return mapping


def resolve_instrument_type(
    type_code: str | None,
    type_map: dict[str, str],
) -> InstrumentType | None:
    """Résout un code de type ticker vers un :class:`InstrumentType` (ou None).

    Renvoie ``None`` si le code est inconnu, si l'asset_class n'est pas gérée
    par MassiVibe (ex: ``crypto``), ou si le type n'est pas *implémenté*
    (cf. :attr:`InstrumentType.implemented` — forex/indices/options sont
    planifiés mais non fonctionnels).

    :param type_code: Code de type (ex: ``"CS"``, ``"ETF"``).
    :param type_map: Mapping code -> asset_class (cf. :func:`build_type_to_asset_class_map`).
    :return: L'InstrumentType correspondant, ou None si non géré/non implémenté.
    """
    if not type_code:
        return None
    asset_class = type_map.get(type_code)
    if asset_class is None:
        return None
    inst_type = _ASSET_CLASS_TO_INSTRUMENT_TYPE.get(asset_class)
    if inst_type is None:
        return None  # crypto (non géré)
    if not inst_type.implemented:
        return None  # forex/indices/options (planifié, non implémenté)
    return inst_type


# ---------------------------------------------------------------------------
# --add-to-config : écriture dans le config.toml utilisateur
# ---------------------------------------------------------------------------


def _existing_instruments_from_toml(config_path: Path) -> dict[str, list[str]]:
    """Lit les listes d'instruments actuelles depuis le config.toml.

    :return: Dict ``{section_name: [symboles]}`` pour les sections du groupe
        ``[instruments]``. Retourne des listes vides si la section est absente.
    """
    import tomllib

    if not config_path.exists():
        return {t.value: [] for t in InstrumentType}

    with open(config_path, "rb") as f:
        data = tomllib.load(f)
    instruments = data.get("instruments", {})
    return {
        InstrumentType.FUTURES.value: list(instruments.get("futures", [])),
        InstrumentType.FOREX.value: list(instruments.get("forex", [])),
        InstrumentType.STOCKS.value: list(instruments.get("stocks", [])),
        InstrumentType.INDICES.value: list(instruments.get("indices", [])),
        InstrumentType.OPTIONS.value: list(instruments.get("options", [])),
    }


def add_to_config(
    search_df: pl.DataFrame,
    types_df: pl.DataFrame,
    settings: Settings,
    *,
    config_path: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Ajoute les tickers de ``search_df`` au ``config.toml`` utilisateur.

    La section cible (``stocks``, ``forex``…) est **déduite** du champ ``type``
    de chaque ticker, résolu en ``asset_class`` via ``types_df`` puis en
    :class:`InstrumentType`. Les tickers dont le type ne mappe pas vers un
    ``InstrumentType`` *implémenté* (crypto, forex/indices/options non
    implémentés) sont ignorés avec un avertissement.

    L'écriture est **non-destructive** pour les symboles existants : on
    déduplique (pas de doublon) et on préserve l'ordre existant (nouveaux
    symboles ajoutés en fin de liste). Un backup ``config.toml.bak`` est créé
    avant l'écriture (écrase le précédent backup).

    :param search_df: DataFrame des tickers sélectionnés (issu de
        :func:`search_tickers`). Doit contenir au moins les colonnes ``ticker``
        et ``type``.
    :param types_df: DataFrame du cache ticker_types (pour résoudre code -> asset_class).
    :param settings: Configuration (utilisé uniquement pour le chemin par défaut).
    :param config_path: Chemin du config.toml cible. Par défaut : config.toml
        utilisateur (``~/.config/massivibe/config.toml``). En tests, on passe un
        chemin temporaire.
    :param dry_run: Si True, n'écrit rien — renvoie juste le récapitulatif.
    :return: Dict récapitulatif :
        ``{"added": {section: [symbols]}, "skipped_unmanaged": [symbols],
        "skipped_duplicates": [symbols], "config_path": str, "backup_path": str|None}``.
    :raises ValueError: Si ``search_df`` ne contient pas les colonnes requises.
    """
    if "ticker" not in search_df.columns or "type" not in search_df.columns:
        raise ValueError(
            "search_df doit contenir les colonnes 'ticker' et 'type' pour --add-to-config."
        )

    _ = settings  # conservé pour l'API ; config_path par défaut via get_user_config_path
    if config_path is None:
        config_path = get_user_config_path()

    type_map = build_type_to_asset_class_map(types_df)

    # Lecture de l'état actuel (pour dédup)
    existing = _existing_instruments_from_toml(config_path)
    existing_upper = {k: {s.upper() for s in v} for k, v in existing.items()}

    added: dict[str, list[str]] = {}
    skipped_unmanaged: list[str] = []
    skipped_duplicates: list[str] = []

    for row in search_df.select("ticker", "type").iter_rows(named=True):
        ticker_sym = str(row["ticker"]).strip().upper()
        type_code = row.get("type")
        type_code = str(type_code) if type_code is not None else None

        inst_type = resolve_instrument_type(type_code, type_map)
        if inst_type is None:
            skipped_unmanaged.append(ticker_sym)
            logger.info(
                f"--add-to-config: ignore {ticker_sym} (type={type_code!r} "
                f"non géré/non implémenté par MassiVibe)"
            )
            continue

        section = inst_type.value
        if ticker_sym in existing_upper[section]:
            skipped_duplicates.append(ticker_sym)
            continue

        added.setdefault(section, []).append(ticker_sym)
        existing[section].append(ticker_sym)
        existing_upper[section].add(ticker_sym)

    backup_path: str | None = None
    if not dry_run and (added or skipped_unmanaged or skipped_duplicates):
        backup_path = _rewrite_config_instruments(config_path, existing)

    logger.info(
        f"--add-to-config: ajouté {sum(len(v) for v in added.values())} ticker(s) "
        f"({added}), ignorés non-gérés={len(skipped_unmanaged)}, "
        f"doublons={len(skipped_duplicates)}, backup={backup_path}"
    )

    return {
        "added": added,
        "skipped_unmanaged": skipped_unmanaged,
        "skipped_duplicates": skipped_duplicates,
        "config_path": str(config_path),
        "backup_path": backup_path,
    }


def _rewrite_config_instruments(
    config_path: Path,
    instruments: dict[str, list[str]],
) -> str | None:
    """Réécrit les listes d'instruments dans le config.toml (section [instruments]).

    Crée un backup ``config.toml.bak`` avant l'écriture. Préserve le reste du
    fichier (commentaires, autres sections) en réécrivant uniquement les lignes
    de la section ``[instruments]``.

    :return: Chemin du backup créé, ou None si rien à écrire / fichier absent
        (crée alors le fichier).
    """
    # Représentation TOML d'une liste de symboles : ["A", "B"]
    def _toml_list(symbols: list[str]) -> str:
        if not symbols:
            return "[]"
        inner = ", ".join(f'"{s}"' for s in symbols)
        return f"[{inner}]"

    new_section_lines = ["[instruments]"]
    # Ordre canonique des sections instruments (cohérent avec config.toml.example)
    for t in (
        InstrumentType.FUTURES,
        InstrumentType.FOREX,
        InstrumentType.STOCKS,
        InstrumentType.INDICES,
        InstrumentType.OPTIONS,
    ):
        new_section_lines.append(f"{t.value} = {_toml_list(instruments.get(t.value, []))}")

    backup_path = config_path.with_suffix(config_path.suffix + ".bak")

    if not config_path.exists():
        # Fichier absent : on crée un config.toml minimal avec la section instruments.
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("\n".join(new_section_lines) + "\n", encoding="utf-8")
        logger.info(f"Créé {config_path} (section [instruments])")
        return None

    # Lecture du fichier existant
    original = config_path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=False)

    # Backup
    backup_path.write_text(original, encoding="utf-8")
    logger.info(f"Backup créé : {backup_path}")

    # On remplace le bloc [instruments] jusqu'à la prochaine section [xxx] (ou EOF).
    out_lines: list[str] = []
    i = 0
    n = len(lines)
    replaced = False
    while i < n:
        line = lines[i]
        if line.strip() == "[instruments]":
            # On injecte la nouvelle section et on saute l'ancienne jusqu'à la
            # prochaine section [xxx] (ligne commençant par '[' hors commentaires).
            out_lines.extend(new_section_lines)
            replaced = True
            i += 1
            # On conserve une ligne vide avant la section suivante si l'original en avait une.
            blank_before_next = False
            while i < n and not _is_section_header(lines[i]):
                if lines[i].strip() == "":
                    blank_before_next = True
                i += 1
            # Si on arrive sur une section suivante et qu'il y avait une ligne
            # vide, on la réinjecte pour préserver la lisibilité.
            if i < n and blank_before_next:
                out_lines.append("")
            continue
        out_lines.append(line)
        i += 1

    if not replaced:
        # La section [instruments] n'existait pas — on l'ajoute à la fin.
        out_lines.append("")
        out_lines.extend(new_section_lines)

    config_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    logger.info(f"config.toml mis à jour : {config_path}")
    return str(backup_path)


def _is_section_header(line: str) -> bool:
    """True si la ligne est un header de section TOML (ex: ``[stocks]``)."""
    s = line.strip()
    return s.startswith("[") and s.endswith("]") and not s.startswith("[[")
