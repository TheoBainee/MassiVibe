"""Cache corporate actions Yahoo (track daily stocks).

Layout ::

    cache/yahoo_actions/{symbol}/splits.parquet
    cache/yahoo_actions/{symbol}/dividends.parquet

Schéma aligné sur Massive pour réutiliser ``apply_split_adjustment`` /
``apply_dividend_adjustment`` :
- splits : ``execution_date``, ``historical_adjustment_factor`` (+ ``split_ratio``)
- dividends : ``ex_dividend_date``, ``historical_adjustment_factor`` (+ ``amount``)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

from myquantstore.api.yahoo import (
    compute_dividend_adjustment_factors,
    compute_split_adjustment_factors,
    fetch_actions,
    fetch_daily_ohlcv,
)
from myquantstore.config import Settings
from myquantstore.instruments import Instrument, InstrumentType
from myquantstore.logging_setup import get_logger, log_cache_skip
from myquantstore.storage.parquet_io import read_meta, read_parquet, write_meta, write_parquet
from myquantstore.tickers.yahoo_map import to_yahoo_ticker

logger = get_logger("yahoo_actions_cache")


class YahooActionsCache:
    """Cache splits/dividends Yahoo pour un symbole stock interne."""

    def __init__(self, symbol: str, kind: str, settings: Settings):
        if kind not in ("splits", "dividends"):
            raise ValueError(f"kind doit être splits|dividends (reçu {kind})")
        self.symbol = symbol
        self.kind = kind
        self._settings = settings
        self._parquet_path = settings.yahoo_actions_path(symbol, kind)

    @property
    def parquet_path(self) -> Path:
        return self._parquet_path

    @property
    def exists(self) -> bool:
        return self._parquet_path.exists()

    def get(
        self,
        *,
        yahoo_ticker: str | None = None,
        force_refresh: bool = False,
        ohlcv: pl.DataFrame | None = None,
    ) -> pl.DataFrame:
        """Lit le cache ou rafraîchit depuis Yahoo.

        :param yahoo_ticker: Override ticker Yahoo (sinon mapping auto).
        :param force_refresh: Ignore le TTL.
        :param ohlcv: Daily OHLCV pour calculer les facteurs dividend (optionnel ;
            si absent au refresh, un history max est fetché pour les dividends).
        """
        if not force_refresh and self._is_fresh():
            meta = read_meta(self._parquet_path)
            last_fetched = meta.get("last_fetched_at", "inconnu") if meta else "inconnu"
            log_cache_skip(logger, f"yahoo_actions.{self.kind}", self.symbol, str(last_fetched))
            return read_parquet(self._parquet_path)

        y_ticker = yahoo_ticker or to_yahoo_ticker(
            Instrument(InstrumentType.STOCKS, self.symbol),
            self._settings.yahoo_ticker_overrides,
        )
        return self.refresh(y_ticker, ohlcv=ohlcv)

    def refresh(
        self,
        yahoo_ticker: str,
        *,
        ohlcv: pl.DataFrame | None = None,
    ) -> pl.DataFrame:
        """Force un refresh depuis Yahoo et écrit le cache."""
        splits_raw, divs_raw = fetch_actions(yahoo_ticker, self._settings)

        if self.kind == "splits":
            df = compute_split_adjustment_factors(splits_raw)
        else:
            bars = ohlcv
            if bars is None or bars.is_empty():
                bars = fetch_daily_ohlcv(
                    yahoo_ticker,
                    self._settings,
                    period="max",
                    internal_symbol=self.symbol,
                )
            df = compute_dividend_adjustment_factors(divs_raw, bars)

        self._write(df, yahoo_ticker)
        return df

    def _is_fresh(self) -> bool:
        if not self._parquet_path.exists():
            return False
        meta = read_meta(self._parquet_path)
        if meta is None:
            return False
        last_fetched_str = meta.get("last_fetched_at")
        if not last_fetched_str:
            return False
        try:
            last_fetched = datetime.fromisoformat(str(last_fetched_str))
        except (ValueError, TypeError):
            return False
        if last_fetched.tzinfo is None:
            last_fetched = last_fetched.replace(tzinfo=UTC)
        age = datetime.now(UTC) - last_fetched
        return age < timedelta(days=self._settings.instrument_cache_ttl_days)

    def _write(self, df: pl.DataFrame, yahoo_ticker: str) -> None:
        extra_meta = {
            "ticker": self.symbol,
            "yahoo_ticker": yahoo_ticker,
            "kind": self.kind,
            "source": "yahoo",
            "last_fetched_at": datetime.now(UTC).isoformat(),
        }
        write_parquet(df, self._parquet_path, **extra_meta)
        meta = read_meta(self._parquet_path)
        if meta and "last_fetched_at" not in meta:
            meta["last_fetched_at"] = extra_meta["last_fetched_at"]
            write_meta(self._parquet_path, meta)
        logger.info(
            f"Cache yahoo_actions.{self.kind} mis à jour: {self._parquet_path} ({df.height} lignes)"
        )

    def get_last_fetched(self) -> datetime | None:
        meta = read_meta(self._parquet_path)
        if meta is None:
            return None
        raw = meta.get("last_fetched_at")
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(str(raw))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except (ValueError, TypeError):
            return None


def refresh_yahoo_actions(
    symbol: str,
    settings: Settings,
    yahoo_ticker: str,
    *,
    ohlcv: pl.DataFrame | None = None,
    force: bool = False,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Rafraîchit splits + dividends Yahoo pour un symbole."""
    sc = YahooActionsCache(symbol, "splits", settings)
    dc = YahooActionsCache(symbol, "dividends", settings)
    if force or not sc._is_fresh():
        splits = sc.refresh(yahoo_ticker, ohlcv=ohlcv)
    else:
        splits = sc.get(yahoo_ticker=yahoo_ticker)
    if force or not dc._is_fresh():
        dividends = dc.refresh(yahoo_ticker, ohlcv=ohlcv)
    else:
        dividends = dc.get(yahoo_ticker=yahoo_ticker, ohlcv=ohlcv)
    return splits, dividends
