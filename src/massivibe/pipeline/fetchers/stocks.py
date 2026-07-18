"""Fetcher stocks — endpoint v2 ``/v2/aggs/ticker/{t}/range/...`` + corporate actions.

Logique d'historisation des stocks :

1. Vérifier "déjà fait aujourd'hui" (skip si un run daté d'aujourd'hui existe).
2. Rafraîchir le cache **splits** (corporate actions) — nécessaire pour
   l'ajustement split à la query (toggle ``--no-split``).
3. Déterminer la plage à fetcher (premier run vs incrémental).
4. Fetch via ``/v2/aggs/ticker/{api_ticker}/range/...`` avec ``adjusted=false``
   (prix bruts — l'ajustement split se fait à la query).
5. Sauvegarder le dump pseudo-brut (ticker = symbole, données normalisées).
6. Agréger les dumps pseudo-bruts.

Pas de RolloverChain (symbole unique, pas d'expiration) — la chaîne est une
:class:`massivibe.chains.SingleSymbolChain` construite à la query.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import polars as pl

from massivibe.api.aggs_v2 import fetch_aggs_v2
from massivibe.api.client import MassiveClient
from massivibe.config import Settings, generate_run_ts
from massivibe.corporate_actions.cache import CorporateActionsCache
from massivibe.instruments import Instrument
from massivibe.logging_setup import get_logger
from massivibe.pipeline.aggregator import aggregate
from massivibe.pipeline.fetchers.base import InstrumentFetcher
from massivibe.storage.aggregate_cache import read_aggregate
from massivibe.storage.raw_dumps import has_run_today, raw_dumps_exist, save_raw_dump

logger = get_logger("fetch.stocks")


class StocksFetcher(InstrumentFetcher):
    """Fetcher pour les stocks (symbole unique + ajustement split à la query)."""

    def fetch(
        self,
        instrument: Instrument,
        settings: Settings,
        client: MassiveClient,
        force: bool = False,
        dry_run: bool = False,
    ) -> dict[str, object]:
        """Historise un stock via l'endpoint v2 (prix bruts adjusted=false)."""
        symbol = instrument.symbol
        logger.info(f"=== stocks:{symbol} ===")
        result: dict[str, object] = {
            "status": "ok",
            "instrument": str(instrument),
            "symbol": symbol,
            "candles": 0,
        }

        # 1. Vérifier "déjà fait aujourd'hui"
        if not force and not dry_run:
            already_done, existing_run_ts = has_run_today(instrument, settings)
            if already_done:
                logger.warning(
                    f"Historisation déjà effectuée aujourd'hui pour {symbol} "
                    f"(run_ts={existing_run_ts}) — skip. Utilisez --force pour relancer."
                )
                result["status"] = "skipped"
                result["existing_run_ts"] = existing_run_ts
                return result

        # 2. Rafraîchir le cache splits (nécessaire pour l'ajustement à la query)
        splits_cache = CorporateActionsCache(symbol, "splits", settings)
        if not dry_run:
            splits_cache.get(client, force_refresh=force)

        # 3. Déterminer la plage à fetcher
        today = datetime.now(UTC).date()
        target_start = today - timedelta(days=settings.history_months_for(instrument.type) * 30)

        has_existing = raw_dumps_exist(instrument, settings)
        oldest_date, latest_date = self._existing_range(instrument, settings, has_existing)

        # Premier run : [target_start, today] ; incrémental : [latest - buffer, today]
        if oldest_date is None:
            cover_start = target_start
        else:
            cover_start = (
                latest_date - timedelta(days=settings.overlap_buffer_days)
                if latest_date
                else target_start
            )
        cover_end = today

        if cover_start >= cover_end:
            logger.warning(f"Rien à fetcher pour {symbol} (cover_start >= cover_end)")
            result["status"] = "no_range"
            return result

        logger.info(
            f"{symbol}: range=[{cover_start}, {cover_end}], historique existant: "
            f"{'oui' if has_existing else 'non'}"
            + (f" (oldest={oldest_date}, latest={latest_date})" if has_existing else "")
        )

        if dry_run:
            logger.info(f"[dry-run] Plan de fetch pour {symbol}: range=[{cover_start}, {cover_end}]")
            result["status"] = "dry_run"
            result["segments"] = [{"ticker": symbol}]
            return result

        # 4. Fetch via l'endpoint v2 (prix bruts)
        run_ts = generate_run_ts()
        df = fetch_aggs_v2(
            client,
            instrument,
            settings,
            date_from=cover_start.isoformat(),
            date_to=cover_end.isoformat(),
        )

        if df.is_empty():
            logger.warning(f"Aucun chandelier pour {symbol} sur [{cover_start}, {cover_end}]")
            result["status"] = "no_candles"
            result["run_ts"] = run_ts
            return result

        # Stamp colonnes identité
        df = df.with_columns(pl.lit(symbol).alias("symbol"))
        df = df.with_columns(pl.lit(instrument.type.value).alias("instrument_type"))
        df = df.with_columns(pl.lit(symbol).alias("product_code"))  # compat agrégateur
        df = df.with_columns(pl.lit(run_ts).alias("run_id"))

        multiplier, timespan = _timeframe_parts(settings.timeframe)
        source_url = (
            f"/v2/aggs/ticker/{instrument.api_ticker}/range/{multiplier}/{timespan}/"
            f"{cover_start.isoformat()}/{cover_end.isoformat()}?adjusted=false"
        )
        save_raw_dump(
            df,
            instrument,
            symbol,  # ticker = symbole (pas de sous-niveau contrat)
            run_ts,
            settings,
            source_url=source_url,
            page_count=client.page_count,
        )

        total_candles = df.height
        result["candles"] = total_candles
        result["run_ts"] = run_ts
        logger.info(f"  {symbol}: {total_candles} chandeliers récupérés")

        # 5. Agréger
        if total_candles > 0 or has_existing:
            logger.info(f"Agrégation de {symbol}...")
            aggregate(instrument, settings)

        logger.info(f"{symbol}: terminé ({total_candles} chandeliers)")
        return result

    @staticmethod
    def _existing_range(
        instrument: Instrument, settings: Settings, has_existing: bool
    ) -> tuple[date | None, date | None]:
        """Retourne (oldest_date, latest_date) de l'agrégé existant."""
        if not has_existing:
            return None, None
        try:
            existing_agg = read_aggregate(instrument, settings)
            if not existing_agg.is_empty() and "window_start" in existing_agg.columns:
                oldest_raw = existing_agg["window_start"].min()
                latest_raw = existing_agg["window_start"].max()
                oldest_date = oldest_raw.date() if isinstance(oldest_raw, datetime) else None
                latest_date = latest_raw.date() if isinstance(latest_raw, datetime) else None
                return oldest_date, latest_date
        except FileNotFoundError:
            pass
        return None, None


def _timeframe_parts(timeframe: str) -> tuple[int, str]:
    """Réutilise parse_timeframe pour le logging du source_url."""
    from massivibe.instruments import parse_timeframe

    return parse_timeframe(timeframe)
