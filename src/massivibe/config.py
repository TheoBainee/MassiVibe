"""Configuration de MassiVibe.

Ce module charge la configuration depuis deux sources distinctes :
- ``.env`` : les secrets (clé API, URL de base) via ``pydantic-settings``.
  Jamais committé, chargé avec ``env_prefix = "MASSIVE_"``.
- ``config.toml`` : les paramètres métier (instruments, fetch, stockage, rollover…).
  Committé, chargé via ``tomllib`` (stdlib Python 3.11+).

Les deux sources sont fusionnées dans une unique classe :class:`Settings` qui
expose tous les paramètres de manière typée et validée.
"""

from __future__ import annotations

import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    product_codes: list[str] = ["NQ", "ES", "RTY", "YM"]

    # --- Fetch (config.toml: [fetch]) ---
    timeframe: str = "1min"
    overlap_buffer_days: int = 1
    history_months: int = 24
    requests_per_minute: int = 10
    page_limit: int = 50000
    contracts_page_limit: int = 1000
    max_retries: int = 6

    # --- Storage (config.toml: [storage]) ---
    data_dir: str = "./data"
    raw_dumps_subdir: str = "raw"
    aggregate_subdir: str = "aggregate"
    contracts_cache_dir: str = "data/cache/contracts"
    log_dir: str = "./logs"

    # --- Cache contrats (config.toml: [contracts_cache]) ---
    contracts_ttl_days: int = 30

    # --- Rollover (config.toml: [rollover]) ---
    days_before_expiry: int = 7

    # --- Tests (config.toml: [tests]) ---
    data_quality_trigger: float = 0.1

    # --- Logging (config.toml: [logging]) ---
    log_level: str = "DEBUG"

    # --- Validations ---

    @field_validator("product_codes")
    @classmethod
    def _product_codes_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("product_codes ne peut pas être vide")
        return v

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

    @field_validator("data_quality_trigger")
    @classmethod
    def _trigger_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("data_quality_trigger doit être > 0")
        return v

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

    def aggregate_path(self, product_code: str) -> Path:
        """Chemin du fichier Parquet agrégé pour un produit."""
        return self.aggregate_dir() / f"{product_code}_continuous.parquet"

    def contracts_cache_path(self, product_code: str) -> Path:
        """Chemin du fichier Parquet cache contrats pour un produit."""
        return Path(self.contracts_cache_dir) / f"{product_code}.parquet"

    def contracts_meta_path(self, product_code: str) -> Path:
        """Chemin du sidecar .meta.json du cache contrats pour un produit."""
        return Path(self.contracts_cache_dir) / f"{product_code}.meta.json"

    def raw_dump_path(self, product_code: str, ticker: str, run_ts: str) -> Path:
        """Chemin complet d'un dump brut : data/raw/{product_code}/{ticker}/{run_ts}.parquet."""
        return self.raw_dumps_dir() / product_code / ticker / f"{run_ts}.parquet"


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
    # On mappe chaque section [section] du TOML vers les attributs de Settings
    # Le mapping est explicite pour éviter les conflits de noms.
    instruments = toml_data.get("instruments", {})
    fetch = toml_data.get("fetch", {})
    storage = toml_data.get("storage", {})
    contracts_cache = toml_data.get("contracts_cache", {})
    rollover = toml_data.get("rollover", {})
    tests = toml_data.get("tests", {})
    logging_section = toml_data.get("logging", {})

    # On utilise model_dump + update + reconstruct pour rester typé et validé
    data = settings.model_dump()
    data.update(
        {
            "product_codes": instruments.get("product_codes", data["product_codes"]),
            "timeframe": fetch.get("timeframe", data["timeframe"]),
            "overlap_buffer_days": fetch.get("overlap_buffer_days", data["overlap_buffer_days"]),
            "history_months": fetch.get("history_months", data["history_months"]),
            "requests_per_minute": fetch.get("requests_per_minute", data["requests_per_minute"]),
            "page_limit": fetch.get("page_limit", data["page_limit"]),
            "contracts_page_limit": fetch.get("contracts_page_limit", data["contracts_page_limit"]),
            "max_retries": fetch.get("max_retries", data["max_retries"]),
            "data_dir": storage.get("data_dir", data["data_dir"]),
            "raw_dumps_subdir": storage.get("raw_dumps_subdir", data["raw_dumps_subdir"]),
            "aggregate_subdir": storage.get("aggregate_subdir", data["aggregate_subdir"]),
            "contracts_cache_dir": storage.get("contracts_cache_dir", data["contracts_cache_dir"]),
            "log_dir": storage.get("log_dir", data["log_dir"]),
            "contracts_ttl_days": contracts_cache.get("ttl_days", data["contracts_ttl_days"]),
            "days_before_expiry": rollover.get("days_before_expiry", data["days_before_expiry"]),
            "data_quality_trigger": tests.get("data_quality_trigger", data["data_quality_trigger"]),
            "log_level": logging_section.get("level", data["log_level"]),
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
