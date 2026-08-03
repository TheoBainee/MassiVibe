"""Taux sans risque dynamique (Yahoo Finance, track 1day).

Source par défaut : indice **^IRX** (13-week T-bill yield). Sur Yahoo le
``close`` est exprimé en **pourcentage** (ex. ``3.70`` → ``0.037`` annualisé).

Fallback : ``settings.portfolio_risk_free_rate`` si le fetch échoue ou si
``portfolio_rf_source = "static"``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl

from myquantstore.api.yahoo import YahooError, fetch_chart_bundle
from myquantstore.config import Settings
from myquantstore.logging_setup import get_logger
from myquantstore.storage.parquet_io import read_meta, read_parquet, write_parquet

logger = get_logger("analytics.risk_free")

# ^IRX = 13-week Treasury bill yield (pourcentage annualisé côté Yahoo)
DEFAULT_RF_YAHOO_TICKER = "^IRX"


@dataclass(slots=True, frozen=True)
class RiskFreeQuote:
    """Snapshot du taux sans risque utilisé pour Sharpe / optim."""

    rate: float  # annualisé, fraction (0.037 = 3.7%)
    source: str  # "yahoo" | "static" | "cli"
    as_of: date | None = None
    yahoo_ticker: str | None = None
    detail: str = ""


def _cache_path(settings: Settings, yahoo_ticker: str) -> Path:
    safe = yahoo_ticker.replace("^", "IDX_").replace("/", "_")
    return Path(settings.cache_dir) / "risk_free" / f"{safe}.parquet"


def _is_cache_fresh(parquet: Path, ttl_days: int) -> bool:
    if not parquet.exists():
        return False
    meta = read_meta(parquet)
    if not meta:
        return False
    raw = meta.get("last_fetched_at")
    if not raw:
        return False
    try:
        fetched = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=UTC)
    except ValueError:
        return False
    age = datetime.now(UTC) - fetched.astimezone(UTC)
    return age <= timedelta(days=ttl_days)


def _yield_pct_to_rate(close_pct: float) -> float:
    """Convertit un close Yahoo yield (%) en fraction annualisée."""
    return float(close_pct) / 100.0


def _quote_from_df(df: pl.DataFrame, ticker: str, detail: str) -> RiskFreeQuote:
    last = df.sort("session_end_date").tail(1)
    close = float(last["close"][0])
    as_of = last["session_end_date"][0]
    rate = _yield_pct_to_rate(close)
    return RiskFreeQuote(
        rate=rate,
        source="yahoo",
        as_of=as_of if isinstance(as_of, date) else None,
        yahoo_ticker=ticker,
        detail=detail,
    )


def fetch_yahoo_risk_free(
    settings: Settings,
    *,
    yahoo_ticker: str | None = None,
    force_refresh: bool = False,
    lookback_days: int = 30,
) -> RiskFreeQuote:
    """Récupère le dernier yield Yahoo et le convertit en taux annualisé.

    :raises YahooError: si le chart est inaccessible et qu'aucun cache n'existe.
    :raises ValueError: si aucune barre valide.
    """
    ticker = yahoo_ticker or settings.portfolio_rf_yahoo_ticker or DEFAULT_RF_YAHOO_TICKER
    parquet = _cache_path(settings, ticker)
    ttl = settings.portfolio_rf_cache_ttl_days

    if not force_refresh and _is_cache_fresh(parquet, ttl):
        df = read_parquet(parquet)
        if df.height > 0 and "close" in df.columns:
            q = _quote_from_df(df, ticker, "cache")
            logger.info(f"RF Yahoo cache hit {ticker}: {q.rate:.4%} as_of={q.as_of}")
            return q

    end = date.today()
    start = end - timedelta(days=max(lookback_days, 7))
    ohlcv, _splits, _divs = fetch_chart_bundle(
        ticker,
        settings,
        date_from=start,
        date_to=end,
        period=None,
        internal_symbol=ticker,
    )
    if ohlcv.is_empty() or "close" not in ohlcv.columns:
        raise ValueError(f"Yahoo RF {ticker}: aucune barre close")

    slim = (
        ohlcv.select(
            [
                pl.col("session_end_date"),
                pl.col("close").cast(pl.Float64),
            ]
        )
        .drop_nulls()
        .sort("session_end_date")
    )
    if slim.is_empty():
        raise ValueError(f"Yahoo RF {ticker}: closes null")

    write_parquet(
        slim,
        parquet,
        kind="risk_free",
        yahoo_ticker=ticker,
        last_fetched_at=datetime.now(UTC).isoformat(),
        unit="percent_yield",
    )

    q = _quote_from_df(slim, ticker, "fetch")
    logger.info(f"RF Yahoo fetch {ticker}: rate={q.rate:.4%} as_of={q.as_of}")
    return q


def resolve_risk_free_rate(
    settings: Settings,
    *,
    cli_rf: float | None = None,
    force_refresh: bool = False,
) -> RiskFreeQuote:
    """Résout le rf effectif : CLI > yahoo (si configuré) > static config.

    Ordre :
    1. ``cli_rf`` si fourni (``--rf``)
    2. Yahoo 1day si ``portfolio_rf_source == "yahoo"``
    3. ``portfolio_risk_free_rate`` (static), y compris en fallback d'erreur Yahoo
    """
    if cli_rf is not None:
        return RiskFreeQuote(rate=float(cli_rf), source="cli", detail="--rf")

    source = (settings.portfolio_rf_source or "static").strip().lower()
    if source == "yahoo":
        try:
            return fetch_yahoo_risk_free(settings, force_refresh=force_refresh)
        except (YahooError, ValueError, OSError) as exc:
            fallback = float(settings.portfolio_risk_free_rate)
            logger.warning(
                f"RF Yahoo indisponible ({exc}) — fallback static {fallback:.4%}"
            )
            return RiskFreeQuote(
                rate=fallback,
                source="static",
                detail=f"fallback after yahoo error: {exc}",
            )

    return RiskFreeQuote(
        rate=float(settings.portfolio_risk_free_rate),
        source="static",
        detail="config portfolio.risk_free_rate",
    )
