"""Fetcher daily stocks via Yahoo Finance (résolution ``1day``).

Historise les chandeliers journaliers bruts + peuplement du cache
``yahoo_actions`` (splits/dividends) pour l'ajustement à la query.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import polars as pl

from myquantstore.api.client import MassiveClient
from myquantstore.api.yahoo import YahooError, fetch_chart_bundle
from myquantstore.config import Settings, generate_run_ts
from myquantstore.instruments import RESOLUTION_1DAY, Instrument, InstrumentType
from myquantstore.logging_setup import get_logger
from myquantstore.pipeline.aggregator import aggregate
from myquantstore.pipeline.fetchers.base import InstrumentFetcher
from myquantstore.storage.aggregate_cache import read_aggregate
from myquantstore.storage.raw_dumps import has_run_today, raw_dumps_exist, save_raw_dump
from myquantstore.tickers.yahoo_map import UnmappableTickerError, to_yahoo_ticker
from myquantstore.yahoo_actions.cache import YahooActionsCache
from myquantstore.api.yahoo import (
    compute_dividend_adjustment_factors,
    compute_split_adjustment_factors,
)

logger = get_logger("fetch.yahoo_daily")


class YahooStocksDailyFetcher(InstrumentFetcher):
    """Fetcher stocks daily (Yahoo) — ignore le client Massive."""

    def fetch(
        self,
        instrument: Instrument,
        settings: Settings,
        client: MassiveClient,
        force: bool = False,
        dry_run: bool = False,
    ) -> dict[str, object]:
        del client  # Yahoo only
        symbol = instrument.symbol
        resolution = RESOLUTION_1DAY
        result: dict[str, object] = {
            "status": "ok",
            "instrument": str(instrument),
            "symbol": symbol,
            "resolution": resolution,
            "source": "yahoo",
            "candles": 0,
        }

        if instrument.type != InstrumentType.STOCKS:
            result["status"] = "skipped"
            result["error"] = "Yahoo daily V1 = stocks only"
            return result

        try:
            y_ticker = to_yahoo_ticker(instrument, settings.yahoo_ticker_overrides)
        except UnmappableTickerError as exc:
            logger.warning(f"Skip Yahoo daily {symbol}: {exc}")
            result["status"] = "skipped"
            result["error"] = str(exc)
            return result

        result["yahoo_ticker"] = y_ticker
        logger.info(f"=== yahoo daily stocks:{symbol} ({y_ticker}) ===")

        if not force and not dry_run:
            already, run_ts = has_run_today(instrument, settings, resolution=resolution)
            if already:
                logger.warning(
                    f"Daily Yahoo déjà fait aujourd'hui pour {symbol} (run_ts={run_ts}) — skip"
                )
                result["status"] = "skipped"
                result["existing_run_ts"] = run_ts
                return result

        today = datetime.now(UTC).date()
        has_existing = raw_dumps_exist(instrument, settings, resolution=resolution)
        oldest_date, latest_date = self._existing_range(instrument, settings, has_existing)

        # Horizon = max Yahoo au premier run ; incrémental ensuite
        if oldest_date is None:
            cover_start: date | None = None  # period=max
            cover_end = today
            use_max = True
        else:
            buffer = settings.yahoo_overlap_buffer_days
            cover_start = (
                latest_date - timedelta(days=buffer) if latest_date else today - timedelta(days=buffer)
            )
            cover_end = today
            use_max = False

        if not use_max and cover_start is not None and cover_start >= cover_end:
            logger.warning(f"Rien à fetcher daily pour {symbol}")
            result["status"] = "no_range"
            return result

        logger.info(
            f"{symbol} daily: "
            + ("period=max" if use_max else f"range=[{cover_start}, {cover_end}]")
            + f", existant={'oui' if has_existing else 'non'}"
        )

        if dry_run:
            result["status"] = "dry_run"
            result["yahoo_ticker"] = y_ticker
            return result

        run_ts = generate_run_ts()
        try:
            if use_max:
                df, splits_raw, divs_raw = fetch_chart_bundle(
                    y_ticker,
                    settings,
                    period="max",
                    internal_symbol=symbol,
                )
            else:
                assert cover_start is not None
                df, splits_raw, divs_raw = fetch_chart_bundle(
                    y_ticker,
                    settings,
                    date_from=cover_start,
                    date_to=cover_end,
                    internal_symbol=symbol,
                )
        except YahooError as exc:
            logger.error(f"Yahoo daily KO {symbol}: {exc}")
            result["status"] = "error"
            result["error"] = str(exc)
            return result

        # Corp actions Yahoo (1 seul round-trip chart déjà fait)
        try:
            splits_df = compute_split_adjustment_factors(splits_raw)
            divs_df = compute_dividend_adjustment_factors(divs_raw, df)
            YahooActionsCache(symbol, "splits", settings)._write(splits_df, y_ticker)
            YahooActionsCache(symbol, "dividends", settings)._write(divs_df, y_ticker)
        except Exception as exc:
            logger.warning(f"yahoo_actions refresh KO {symbol}: {exc}")

        if df.is_empty():
            result["status"] = "no_candles"
            result["run_ts"] = run_ts
            return result

        df = df.with_columns(
            [
                pl.lit(symbol).alias("symbol"),
                pl.lit(instrument.type.value).alias("instrument_type"),
                pl.lit(symbol).alias("product_code"),
                pl.lit(run_ts).alias("run_id"),
            ]
        )

        save_raw_dump(
            df,
            instrument,
            symbol,
            run_ts,
            settings,
            source_url=f"yfinance://{y_ticker}/history",
            page_count=1,
            resolution=resolution,
            source="yahoo",
        )

        result["candles"] = df.height
        result["run_ts"] = run_ts

        if df.height > 0 or has_existing:
            aggregate(instrument, settings, resolution=resolution)

        logger.info(f"{symbol} daily: terminé ({df.height} barres)")
        return result

    @staticmethod
    def _existing_range(
        instrument: Instrument,
        settings: Settings,
        has_existing: bool,
    ) -> tuple[date | None, date | None]:
        if not has_existing:
            return None, None
        try:
            agg = read_aggregate(instrument, settings, resolution=RESOLUTION_1DAY)
            if not agg.is_empty() and "window_start" in agg.columns:
                oldest_raw = agg["window_start"].min()
                latest_raw = agg["window_start"].max()
                oldest = oldest_raw.date() if isinstance(oldest_raw, datetime) else None
                latest = latest_raw.date() if isinstance(latest_raw, datetime) else None
                return oldest, latest
        except FileNotFoundError:
            pass
        return None, None
