"""Configuration de MassiVibe.

Ce module charge la configuration depuis deux sources distinctes :
- ``.env`` : les secrets (clé API, URL de base) via ``pydantic-settings``.
  Jamais committé, chargé avec ``env_prefix = "MASSIVE_"``.
- ``config.toml`` : les paramètres métier (instruments, fetch, stockage, rollover…).
  Committé, chargé via ``tomllib`` (stdlib Python 3.11+).

Les deux sources sont fusionnées dans une unique classe :class:`Settings` qui
expose tous les paramètres de manière typée et validée.

**Structure multi-type** : les instruments sont déclarés par type dans la section
``[instruments]`` (``futures``, ``forex``, ``stocks``, ``indices``, ``options``).
Les paramètres spécifiques à un type vivent dans leur propre section
(``[futures]``, ``[stocks]``) pour éviter de polluer la config générique.
"""

from __future__ import annotations

import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from massivibe.instruments import Instrument, InstrumentType


class Settings(BaseSettings):
    """Configuration globale de MassiVibe.

    Les attributs préfixés par ``MASSIVE_`` sont chargés depuis ``.env``.
    Les autres attributs sont hydratés depuis ``config.toml`` par la fonction
    :func:`load_settings`.
    """

    model_config = SettingsConfigDict(
        env_prefix="MASSIVE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Secrets (chargés depuis .env) ---
    api_key: str = ""
    base_url: str = "https://api.massive.com"

    # --- Instruments (config.toml: [instruments]) ---
    # Listes compactes par type. Les symboles sont nus (ex: "ES", "AAPL", "EURUSD").
    futures: list[str] = ["NQ", "ES", "RTY", "YM"]
    forex: list[str] = []
    stocks: list[str] = []
    indices: list[str] = []
    options: list[str] = []

    # --- Fetch (config.toml: [fetch]) — générique, commun aux aggs de tous les types ---
    timeframe: str = "1min"
    overlap_buffer_days: int = 1
    history_months: int = 24
    requests_per_minute: int = 10
    page_limit: int = 50000  # max 50000 pour les aggs (futures /v1 et v2)
    max_retries: int = 6

    # --- Storage (config.toml: [storage]) ---
    data_dir: str = "./data"
    raw_dumps_subdir: str = "raw"
    aggregate_subdir: str = "aggregate"
    # Racine des caches de listing / métadonnées (contrats, corporate actions…)
    cache_dir: str = "./cache"
    contracts_cache_subdir: str = "contracts"  # cache contrats futures
    corporate_actions_cache_subdir: str = "corporate_actions"  # cache splits/dividends stocks
    log_dir: str = "./logs"

    # --- Cache instruments (config.toml: [instrument_cache]) — TTL commun à tous les caches ---
    instrument_cache_ttl_days: int = 30

    # --- Futures (config.toml: [futures]) — spécifique au type futures ---
    days_before_expiry: int = 7
    contracts_page_limit: int = 1000  # max API = 1000 pour /futures/v1/contracts
    # Intervalle (en mois) entre snapshots pour récupérer les contrats expirés.
    contracts_snapshot_interval_months: int = 3

    # --- Stocks (config.toml: [stocks]) — spécifique au type stocks ---
    splits_page_limit: int = 5000  # max API = 5000 pour /stocks/v1/splits
    dividends_page_limit: int = 5000  # max API = 5000 pour /stocks/v1/dividends

    # --- Tests (config.toml: [tests]) ---
    data_quality_trigger: float = 0.1

    # --- Logging (config.toml: [logging]) ---
    log_level: str = "DEBUG"

    # --- Affichage (config.toml: [display]) ---
    # Limites d'affichage des tableaux Polars dans les commandes CLI
    # (status, contracts, query). Au-delà, le tableau est tronqué.
    display_max_rows: int = 50
    display_max_columns: int = 20

    # --- Chart / Visualisation (config.toml: [chart]) ---
    # Paramètres du serveur de visualisation (commande `massivibe chart`).
    default_timescale_unit: str = "min"
    default_timescale_nb: int = 1
    default_nb_candle: int = 50000
    max_visible_candles: int = 50000
    buffer_multiplier: int = 3
    fetch_chunk_size: int = 50000
    chart_port: int = 8050
    chart_host: str = "127.0.0.1"
    chart_mdns: bool = False

    # --- Validations ---

    @field_validator("overlap_buffer_days")
    @classmethod
    def _buffer_non_neg(cls, v: int) -> int:
        if v < 0:
            raise ValueError("overlap_buffer_days doit être >= 0")
        return v

    @field_validator("days_before_expiry")
    @classmethod
    def _days_before_non_neg(cls, v: int) -> int:
        if v < 0:
            raise ValueError("days_before_expiry doit être >= 0")
        return v

    @field_validator("history_months")
    @classmethod
    def _history_ge_1(cls, v: int) -> int:
        if v < 1:
            raise ValueError("history_months doit être >= 1")
        return v

    @field_validator("requests_per_minute")
    @classmethod
    def _rpm_non_neg(cls, v: int) -> int:
        if v < 0:
            raise ValueError("requests_per_minute doit être >= 0")
        return v

    @field_validator("max_retries")
    @classmethod
    def _retries_ge_1(cls, v: int) -> int:
        if v < 1:
            raise ValueError("max_retries doit être >= 1")
        return v

    @field_validator("page_limit")
    @classmethod
    def _page_limit_range(cls, v: int) -> int:
        if not 1 <= v <= 50000:
            raise ValueError("page_limit doit être entre 1 et 50000")
        return v

    @field_validator("contracts_page_limit")
    @classmethod
    def _contracts_page_limit_range(cls, v: int) -> int:
        if not 1 <= v <= 1000:
            raise ValueError("contracts_page_limit doit être entre 1 et 1000")
        return v

    @field_validator("splits_page_limit", "dividends_page_limit")
    @classmethod
    def _corp_actions_page_limit_range(cls, v: int) -> int:
        if not 1 <= v <= 5000:
            raise ValueError("splits/dividends_page_limit doit être entre 1 et 5000")
        return v

    @field_validator("data_quality_trigger")
    @classmethod
    def _trigger_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("data_quality_trigger doit être > 0")
        return v

    @field_validator("instrument_cache_ttl_days")
    @classmethod
    def _ttl_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("instrument_cache_ttl_days doit être >= 1")
        return v

    @field_validator("display_max_rows", "display_max_columns")
    @classmethod
    def _display_limits_ge_1(cls, v: int) -> int:
        if v < 1:
            raise ValueError("display_max_rows et display_max_columns doivent être >= 1")
        return v

    @field_validator("default_timescale_unit")
    @classmethod
    def _timescale_unit_valid(cls, v: str) -> str:
        if v not in ("min", "hour"):
            raise ValueError(
                f"default_timescale_unit doit être 'min' ou 'hour' (reçu: {v}). "
                "'sec' et 'day' ne sont pas implémentés."
            )
        return v

    @field_validator(
        "default_timescale_nb", "max_visible_candles", "buffer_multiplier",
        "fetch_chunk_size", "default_nb_candle",
    )
    @classmethod
    def _chart_positive_int(cls, v: int) -> int:
        if v < 1:
            raise ValueError("les paramètres chart doivent être >= 1")
        return v

    @model_validator(mode="after")
    def _check_at_least_one_instrument(self) -> Settings:
        """Vérifie qu'au moins un instrument est configuré (tous types confondus)."""
        all_lists = (self.futures, self.forex, self.stocks, self.indices, self.options)
        if all(len(lst) == 0 for lst in all_lists):
            raise ValueError(
                "Aucun instrument configuré. Déclarez au moins un symbole dans "
                "[instruments] (futures, forex, stocks, indices ou options)."
            )
        return self

    @model_validator(mode="after")
    def _check_api_key(self) -> Settings:
        """Avertit si la clé API est absente — elle est obligatoire pour les appels API."""
        if not self.api_key:
            # On ne lève pas d'erreur ici car certaines commandes (config, setup-key)
            # n'ont pas besoin de clé. L'erreur sera levée au moment de l'appel API.
            pass
        return self

    # --- Helpers de chemins ---

    def raw_dumps_dir(self) -> Path:
        """Chemin complet du répertoire des dumps bruts."""
        return Path(self.data_dir) / self.raw_dumps_subdir

    def aggregate_dir(self) -> Path:
        """Chemin complet du répertoire du cache agrégé."""
        return Path(self.data_dir) / self.aggregate_subdir

    def aggregate_path(self, instrument: Instrument) -> Path:
        """Chemin du fichier Parquet agrégé pour un instrument.

        Layout : ``data/aggregate/{type}/{symbol}.parquet``
        Le suffixe ``_continuous`` (futures) est abandonné au profit d'un nom
        neutre — la logique de continu/rollover se fait à la query, pas au stockage.
        """
        return self.aggregate_dir() / instrument.path_segment / f"{instrument.symbol}.parquet"

    def raw_dump_path(self, instrument: Instrument, ticker: str, run_ts: str) -> Path:
        """Chemin complet d'un dump brut.

        Layout : ``data/raw/{type}/{symbol}/{ticker}/{run_ts}.parquet``

        Pour futures, ``ticker`` = le contrat individuel (ex: ``ESM5``).
        Pour les autres types, ``ticker`` = le symbole (pas de sous-niveau
        contrat) — on passe ``ticker=instrument.symbol``.
        """
        return (
            self.raw_dumps_dir()
            / instrument.path_segment
            / instrument.symbol
            / ticker
            / f"{run_ts}.parquet"
        )

    # --- Caches ---

    def contracts_cache_dir(self) -> Path:
        """Chemin du répertoire du cache contrats futures."""
        return Path(self.cache_dir) / self.contracts_cache_subdir

    def contracts_cache_path(self, product_code: str) -> Path:
        """Chemin du fichier Parquet cache contrats pour un produit futures."""
        return self.contracts_cache_dir() / f"{product_code}.parquet"

    def contracts_meta_path(self, product_code: str) -> Path:
        """Chemin du sidecar .meta.json du cache contrats pour un produit futures."""
        return self.contracts_cache_dir() / f"{product_code}.meta.json"

    def corporate_actions_dir(self) -> Path:
        """Chemin du répertoire racine du cache corporate actions (stocks)."""
        return Path(self.cache_dir) / self.corporate_actions_cache_subdir

    def corporate_actions_path(self, ticker: str, kind: str) -> Path:
        """Chemin du fichier Parquet corporate actions pour un ticker.

        :param ticker: Symbole nu du stock (ex: ``"AAPL"``).
        :param kind: Type d'action : ``"splits"`` ou ``"dividends"``.
        :return: ``cache/corporate_actions/{ticker}/{kind}.parquet``
        """
        return self.corporate_actions_dir() / ticker / f"{kind}.parquet"

    def corporate_actions_meta_path(self, ticker: str, kind: str) -> Path:
        """Chemin du sidecar .meta.json du cache corporate actions."""
        return self.corporate_actions_dir() / ticker / f"{kind}.meta.json"

    # --- Helpers d'instruments ---

    def instruments_of_type(self, t: InstrumentType) -> list[Instrument]:
        """Retourne la liste des instruments configurés d'un type donné."""
        symbols = self._symbols_for_type(t)
        return [Instrument(type=t, symbol=s) for s in symbols]

    def all_instruments(self) -> list[Instrument]:
        """Retourne tous les instruments configurés (tous types confondus)."""
        result: list[Instrument] = []
        for t in InstrumentType:
            result.extend(self.instruments_of_type(t))
        return result

    def resolve_instrument(self, symbol: str, t: InstrumentType | None = None) -> Instrument:
        """Résout un symbole en :class:`Instrument` depuis la config.

        :param symbol: Symbole nu (ex: ``"ES"``, ``"AAPL"``).
        :param t: Type imposé. Si None, cherche le symbole parmi tous les types
            configurés et lève une erreur si le symbole est absent ou ambigu
            (présent dans plusieurs types).
        :return: L'instrument résolu.
        :raises ValueError: Si le symbole n'est pas configuré, ou est ambigu sans
            type imposé.
        """
        if t is not None:
            if symbol in self._symbols_for_type(t):
                return Instrument(type=t, symbol=symbol)
            raise ValueError(
                f"Symbole '{symbol}' non trouvé dans les instruments de type '{t.value}'."
            )

        found: list[Instrument] = []
        for tt in InstrumentType:
            if symbol in self._symbols_for_type(tt):
                found.append(Instrument(type=tt, symbol=symbol))
        if not found:
            raise ValueError(
                f"Symbole '{symbol}' non trouvé dans les instruments configurés. "
                f"Instruments: {[str(i) for i in self.all_instruments()]}"
            )
        if len(found) > 1:
            raise ValueError(
                f"Symbole '{symbol}' ambigu — présent dans plusieurs types: "
                f"{[i.type.value for i in found]}. Précisez --type."
            )
        return found[0]

    def _symbols_for_type(self, t: InstrumentType) -> list[str]:
        """Retourne la liste des symboles configurés pour un type."""
        return {
            InstrumentType.FUTURES: self.futures,
            InstrumentType.FOREX: self.forex,
            InstrumentType.STOCKS: self.stocks,
            InstrumentType.INDICES: self.indices,
            InstrumentType.OPTIONS: self.options,
        }[t]


def load_settings(config_path: str | Path | None = None) -> Settings:
    """Charge la configuration depuis ``.env`` + ``config.toml``.

    :param config_path: Chemin du fichier ``config.toml``.
        Par défaut : ``config.toml`` dans le répertoire courant.
    :return: Instance :class:`Settings` complète.
    :raises FileNotFoundError: Si ``config.toml`` n'existe pas.
    """
    # 1. Charger les secrets depuis .env (pydantic-settings)
    settings = Settings()

    # 2. Charger config.toml
    if config_path is None:
        config_path = Path("config.toml")
    config_path = Path(config_path)

    if not config_path.exists():
        raise FileNotFoundError(
            f"Fichier de configuration introuvable : {config_path}. "
            "Créez config.toml (voir config.toml dans le dépôt)."
        )

    with open(config_path, "rb") as f:
        toml_data: dict[str, Any] = tomllib.load(f)

    # 3. Hydrater settings avec les valeurs de config.toml
    # Le mapping est explicite pour éviter les conflits de noms.
    instruments = toml_data.get("instruments", {})
    futures_cfg = toml_data.get("futures", {})
    stocks_cfg = toml_data.get("stocks", {})
    instrument_cache = toml_data.get("instrument_cache", {})
    fetch = toml_data.get("fetch", {})
    storage = toml_data.get("storage", {})
    tests = toml_data.get("tests", {})
    logging_section = toml_data.get("logging", {})
    display = toml_data.get("display", {})
    chart = toml_data.get("chart", {})

    # On utilise model_dump + update + reconstruct pour rester typé et validé
    data = settings.model_dump()
    data.update(
        {
            # [instruments] — listes par type
            "futures": instruments.get("futures", data["futures"]),
            "forex": instruments.get("forex", data["forex"]),
            "stocks": instruments.get("stocks", data["stocks"]),
            "indices": instruments.get("indices", data["indices"]),
            "options": instruments.get("options", data["options"]),
            # [futures] — spécifique futures
            "days_before_expiry": futures_cfg.get("days_before_expiry", data["days_before_expiry"]),
            "contracts_page_limit": futures_cfg.get("contracts_page_limit", data["contracts_page_limit"]),
            "contracts_snapshot_interval_months": futures_cfg.get(
                "contracts_snapshot_interval_months", data["contracts_snapshot_interval_months"]
            ),
            # [stocks] — spécifique stocks
            "splits_page_limit": stocks_cfg.get("splits_page_limit", data["splits_page_limit"]),
            "dividends_page_limit": stocks_cfg.get("dividends_page_limit", data["dividends_page_limit"]),
            # [instrument_cache] — TTL commun
            "instrument_cache_ttl_days": instrument_cache.get("ttl_days", data["instrument_cache_ttl_days"]),
            # [fetch] — générique
            "timeframe": fetch.get("timeframe", data["timeframe"]),
            "overlap_buffer_days": fetch.get("overlap_buffer_days", data["overlap_buffer_days"]),
            "history_months": fetch.get("history_months", data["history_months"]),
            "requests_per_minute": fetch.get("requests_per_minute", data["requests_per_minute"]),
            "page_limit": fetch.get("page_limit", data["page_limit"]),
            "max_retries": fetch.get("max_retries", data["max_retries"]),
            # [storage]
            "data_dir": storage.get("data_dir", data["data_dir"]),
            "raw_dumps_subdir": storage.get("raw_dumps_subdir", data["raw_dumps_subdir"]),
            "aggregate_subdir": storage.get("aggregate_subdir", data["aggregate_subdir"]),
            "cache_dir": storage.get("cache_dir", data["cache_dir"]),
            "log_dir": storage.get("log_dir", data["log_dir"]),
            # [tests]
            "data_quality_trigger": tests.get("data_quality_trigger", data["data_quality_trigger"]),
            # [logging]
            "log_level": logging_section.get("level", data["log_level"]),
            # [display]
            "display_max_rows": display.get("max_rows", data["display_max_rows"]),
            "display_max_columns": display.get("max_columns", data["display_max_columns"]),
            # [chart]
            "default_timescale_unit": chart.get("default_timescale_unit", data["default_timescale_unit"]),
            "default_timescale_nb": chart.get("default_timescale_nb", data["default_timescale_nb"]),
            "default_nb_candle": chart.get("default_nb_candle", data["default_nb_candle"]),
            "max_visible_candles": chart.get("max_visible_candles", data["max_visible_candles"]),
            "buffer_multiplier": chart.get("buffer_multiplier", data["buffer_multiplier"]),
            "fetch_chunk_size": chart.get("fetch_chunk_size", data["fetch_chunk_size"]),
            "chart_port": chart.get("port", data["chart_port"]),
            "chart_host": chart.get("host", data["chart_host"]),
            "chart_mdns": chart.get("mdns", data["chart_mdns"]),
        }
    )

    # Reconstruire avec validation complète (re-run des field_validators)
    return Settings(**data)


def generate_run_ts() -> str:
    """Génère un identifiant d'exécution au format ``YYYYMMDDTHHMMSS``.

    Exemple : ``20260711T183000`` pour le 11 juillet 2026 à 18:30:00 UTC.
    Ce format garantit l'unicité d'un run et permet de détecter si une historisation
    a déjà été faite aujourd'hui (en comparant les 8 premiers caractères = date).
    """
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
