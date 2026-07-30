"""Client Yahoo Finance via l'API chart publique (curl_cffi).

**Pourquoi pas yfinance seul ?** yfinance ≥0.2 tente d'abord ``fc.yahoo.com``
pour un cookie/crumb. Sur beaucoup de réseaux ce host est injoignable
(connection refused) → faux négatif « delisted ». L'endpoint
``query1/2.finance.yahoo.com/v8/finance/chart/{ticker}`` fonctionne sans crumb
avec impersonation navigateur (curl_cffi).

Normalise vers le schéma canonique MyQuantStore :
- OHLC du chart Yahoo sont **déjà split-adjusted** (pas d'équivalent ``adjusted=false``).
  Le fetcher daily les **désajuste** via les splits avant dump (prix bruts stockés).
- ``window_start`` = minuit UTC de la date de séance
- ``session_end_date`` = date de séance
- actions : splits (ratio) + dividends (montant) via ``events`` du chart
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import quote

import polars as pl

from myquantstore.config import Settings
from myquantstore.logging_setup import get_logger

logger = get_logger("api.yahoo")

_lock = threading.Lock()
_last_call_monotonic: float = 0.0

_CHART_HOSTS = (
    "https://query1.finance.yahoo.com",
    "https://query2.finance.yahoo.com",
)

# Début « max » historique actions US (assez tôt pour toutes les cotations)
_EPOCH_START = datetime(1970, 1, 1, tzinfo=UTC)


class YahooError(RuntimeError):
    """Erreur d'accès Yahoo Finance."""


def _throttle(settings: Settings) -> None:
    """Respecte ``yahoo_requests_per_minute`` (0 = pas de throttle)."""
    global _last_call_monotonic
    rpm = settings.yahoo_requests_per_minute
    if rpm <= 0:
        return
    min_interval = 60.0 / float(rpm)
    with _lock:
        now = time.monotonic()
        wait = min_interval - (now - _last_call_monotonic)
        if wait > 0:
            time.sleep(wait)
        _last_call_monotonic = time.monotonic()


def _import_curl_requests() -> Any:
    try:
        from curl_cffi import requests
    except ImportError as exc:
        raise YahooError(
            "Le package 'curl_cffi' est requis pour Yahoo Finance. "
            "Installez-le : pip install curl_cffi"
        ) from exc
    return requests


def _chart_request(
    yahoo_ticker: str,
    settings: Settings,
    *,
    period1: int,
    period2: int,
    max_retries: int = 4,
) -> dict[str, Any]:
    """GET chart JSON avec impersonation Chrome + retry 429/5xx."""
    requests = _import_curl_requests()
    path = f"/v8/finance/chart/{quote(yahoo_ticker, safe='')}"
    params = {
        "interval": "1d",
        "events": "div,splits",
        "includePrePost": "false",
        "period1": str(period1),
        "period2": str(period2),
    }

    last_err: Exception | None = None
    for attempt in range(max_retries):
        _throttle(settings)
        for host in _CHART_HOSTS:
            url = f"{host}{path}"
            try:
                resp = requests.get(
                    url,
                    params=params,
                    impersonate="chrome",
                    timeout=60,
                )
            except Exception as exc:
                last_err = exc
                logger.warning(f"Yahoo chart réseau {yahoo_ticker} @ {host}: {exc}")
                continue

            if resp.status_code == 200:
                try:
                    payload = resp.json()
                except Exception as exc:
                    last_err = exc
                    continue
                err = (payload.get("chart") or {}).get("error")
                if err:
                    raise YahooError(
                        f"Yahoo chart error pour {yahoo_ticker}: {err}"
                    )
                return payload  # type: ignore[no-any-return]

            if resp.status_code == 429:
                wait = min(60.0, 2.0 ** attempt + 1.0)
                logger.warning(
                    f"Yahoo 429 {yahoo_ticker} (attempt {attempt + 1}/{max_retries}) "
                    f"— sleep {wait:.1f}s"
                )
                time.sleep(wait)
                last_err = YahooError(f"HTTP 429 pour {yahoo_ticker}")
                break  # retry outer loop with backoff

            if resp.status_code >= 500:
                last_err = YahooError(f"HTTP {resp.status_code} pour {yahoo_ticker}")
                time.sleep(1.0 + attempt)
                continue

            # 404 / autres
            body = (resp.text or "")[:200]
            raise YahooError(
                f"Yahoo chart HTTP {resp.status_code} pour {yahoo_ticker}: {body}"
            )

    raise YahooError(
        f"Yahoo chart inaccessible pour {yahoo_ticker} après {max_retries} tentatives"
        + (f" ({last_err})" if last_err else "")
    )


def _period_bounds(
    date_from: date | None,
    date_to: date | None,
    period: str | None,
) -> tuple[int, int]:
    """Calcule (period1, period2) unix UTC pour l'API chart."""
    now = datetime.now(UTC)
    period2 = int(now.timestamp()) + 86400  # marge fin de journée

    if date_from is not None and date_to is not None:
        p1 = int(datetime(date_from.year, date_from.month, date_from.day, tzinfo=UTC).timestamp())
        # end exclusive-ish : lendemain de date_to
        end_d = date_to + timedelta(days=1)
        p2 = int(datetime(end_d.year, end_d.month, end_d.day, tzinfo=UTC).timestamp())
        return p1, max(p2, p1 + 86400)

    # period=max (défaut) ou autres heuristiques
    p = (period or "max").lower()
    if p == "max":
        return int(_EPOCH_START.timestamp()), period2

    # yfinance-like: 1d 5d 1mo 3mo 6mo 1y 2y 5y 10y ytd
    days_map = {
        "1d": 1,
        "5d": 5,
        "1mo": 31,
        "3mo": 93,
        "6mo": 186,
        "1y": 366,
        "2y": 732,
        "5y": 5 * 366,
        "10y": 10 * 366,
        "ytd": max(1, (now.date() - date(now.year, 1, 1)).days + 1),
    }
    n_days = days_map.get(p)
    if n_days is None:
        logger.warning(f"period Yahoo inconnu '{period}' — fallback max")
        return int(_EPOCH_START.timestamp()), period2
    p1 = int((now - timedelta(days=n_days)).timestamp())
    return p1, period2


def _parse_chart_result(
    payload: dict[str, Any],
    *,
    internal_symbol: str,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Parse payload chart → (ohlcv, splits, dividends)."""
    chart = payload.get("chart") or {}
    results = chart.get("result")
    if not results:
        return _empty_ohlcv(), _empty_splits(), _empty_dividends()

    result = results[0]
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []

    rows_ws: list[datetime] = []
    rows_sed: list[date] = []
    rows_o: list[float | None] = []
    rows_h: list[float | None] = []
    rows_l: list[float | None] = []
    rows_c: list[float | None] = []
    rows_v: list[int] = []

    n = len(timestamps)
    for i in range(n):
        c = closes[i] if i < len(closes) else None
        o = opens[i] if i < len(opens) else None
        h = highs[i] if i < len(highs) else None
        lo = lows[i] if i < len(lows) else None
        # Barre invalide (tous null)
        if c is None and o is None and h is None and lo is None:
            continue
        ts = int(timestamps[i])
        dt_utc = datetime.fromtimestamp(ts, tz=UTC)
        sed = dt_utc.date()
        rows_sed.append(sed)
        rows_ws.append(datetime(sed.year, sed.month, sed.day))
        rows_o.append(float(o) if o is not None else None)
        rows_h.append(float(h) if h is not None else None)
        rows_l.append(float(lo) if lo is not None else None)
        rows_c.append(float(c) if c is not None else None)
        v = volumes[i] if i < len(volumes) else None
        rows_v.append(int(v) if v is not None else 0)

    if not rows_ws:
        ohlcv = _empty_ohlcv()
    else:
        ohlcv = pl.DataFrame(
            {
                "window_start": rows_ws,
                "session_end_date": rows_sed,
                "ticker": [internal_symbol] * len(rows_ws),
                "open": rows_o,
                "high": rows_h,
                "low": rows_l,
                "close": rows_c,
                "volume": rows_v,
            }
        ).with_columns(
            [
                pl.col("window_start").cast(pl.Datetime("ns")),
                pl.col("session_end_date").cast(pl.Date),
                pl.col("open").cast(pl.Float64),
                pl.col("high").cast(pl.Float64),
                pl.col("low").cast(pl.Float64),
                pl.col("close").cast(pl.Float64),
                pl.col("volume").cast(pl.Int64),
            ]
        ).drop_nulls(subset=["open", "high", "low", "close"]).sort("window_start")

    events = result.get("events") or {}
    splits = _parse_splits_events(events.get("splits") or {})
    dividends = _parse_dividend_events(events.get("dividends") or {})
    return ohlcv, splits, dividends


def _parse_splits_events(splits_map: dict[str, Any]) -> pl.DataFrame:
    if not splits_map:
        return _empty_splits()
    dates: list[date] = []
    ratios: list[float] = []
    for item in splits_map.values():
        if not isinstance(item, dict):
            continue
        ts = item.get("date")
        if ts is None:
            continue
        num = float(item.get("numerator") or 0.0)
        den = float(item.get("denominator") or 0.0)
        if den == 0.0:
            # fallback splitRatio "2:1"
            sr = str(item.get("splitRatio") or "")
            if ":" in sr:
                a, b = sr.split(":", 1)
                try:
                    num, den = float(a), float(b)
                except ValueError:
                    continue
            else:
                continue
        ratio = num / den
        if ratio <= 0:
            continue
        dates.append(datetime.fromtimestamp(int(ts), tz=UTC).date())
        ratios.append(ratio)
    if not dates:
        return _empty_splits()
    return pl.DataFrame({"execution_date": dates, "split_ratio": ratios}).sort(
        "execution_date"
    )


def _parse_dividend_events(div_map: dict[str, Any]) -> pl.DataFrame:
    if not div_map:
        return _empty_dividends()
    dates: list[date] = []
    amounts: list[float] = []
    for item in div_map.values():
        if not isinstance(item, dict):
            continue
        ts = item.get("date")
        amt = item.get("amount")
        if ts is None or amt is None:
            continue
        dates.append(datetime.fromtimestamp(int(ts), tz=UTC).date())
        amounts.append(float(amt))
    if not dates:
        return _empty_dividends()
    return pl.DataFrame({"ex_dividend_date": dates, "amount": amounts}).sort(
        "ex_dividend_date"
    )


def fetch_chart_bundle(
    yahoo_ticker: str,
    settings: Settings,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    period: str | None = "max",
    internal_symbol: str | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Un seul appel chart → ``(ohlcv, splits, dividends)`` bruts."""
    symbol = internal_symbol or yahoo_ticker
    p1, p2 = _period_bounds(date_from, date_to, period)
    logger.info(
        f"Yahoo chart {yahoo_ticker} period1={p1} period2={p2} "
        f"(from={date_from} to={date_to} period={period if date_from is None else None})"
    )
    payload = _chart_request(yahoo_ticker, settings, period1=p1, period2=p2)
    ohlcv, splits, dividends = _parse_chart_result(payload, internal_symbol=symbol)
    logger.info(
        f"Yahoo {yahoo_ticker}: {ohlcv.height} barres, "
        f"{splits.height} split(s), {dividends.height} dividend(s)"
    )
    return ohlcv, splits, dividends


def fetch_daily_ohlcv(
    yahoo_ticker: str,
    settings: Settings,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    period: str | None = "max",
    internal_symbol: str | None = None,
) -> pl.DataFrame:
    """Récupère l'historique daily brut d'un ticker Yahoo."""
    ohlcv, _, _ = fetch_chart_bundle(
        yahoo_ticker,
        settings,
        date_from=date_from,
        date_to=date_to,
        period=period,
        internal_symbol=internal_symbol,
    )
    return ohlcv


def fetch_actions(
    yahoo_ticker: str,
    settings: Settings,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Récupère splits et dividends bruts Yahoo (sans facteurs cumulés).

    :return: ``(splits_df, dividends_df)``
    """
    _, splits, dividends = fetch_chart_bundle(
        yahoo_ticker,
        settings,
        period="max",
    )
    return splits, dividends


def compute_split_adjustment_factors(splits: pl.DataFrame) -> pl.DataFrame:
    """Ajoute ``historical_adjustment_factor`` cumulatif (compatible adjust.py).

    Convention Yahoo : ratio 4.0 = split 4-for-1 → prix pré-split × (1/4).
    Le facteur sur une ligne = produit des (1/ratio) de ce split et de tous
    les splits **postérieurs** (back-adjust vers aujourd'hui).
    """
    if splits is None or splits.is_empty():
        return pl.DataFrame(
            schema={
                "execution_date": pl.Date,
                "split_ratio": pl.Float64,
                "historical_adjustment_factor": pl.Float64,
            }
        )

    s = splits.sort("execution_date", descending=True)
    ratios = s["split_ratio"].to_list()
    factors: list[float] = []
    cum = 1.0
    for r in ratios:
        ratio = float(r) if r is not None and float(r) != 0.0 else 1.0
        cum *= 1.0 / ratio
        factors.append(cum)

    out = s.with_columns(pl.Series("historical_adjustment_factor", factors))
    return out.sort("execution_date")


def compute_dividend_adjustment_factors(
    dividends: pl.DataFrame,
    ohlcv: pl.DataFrame,
) -> pl.DataFrame:
    """Calcule ``historical_adjustment_factor`` dividend via closes OHLCV.

    Facteur unitaire à l'ex-date : ``1 - amount / prev_close`` (si valide).
    Cumul backward comme pour les splits.
    """
    empty_schema = {
        "ex_dividend_date": pl.Date,
        "amount": pl.Float64,
        "historical_adjustment_factor": pl.Float64,
    }
    if dividends is None or dividends.is_empty():
        return pl.DataFrame(schema=empty_schema)
    if ohlcv is None or ohlcv.is_empty() or "close" not in ohlcv.columns:
        return dividends.with_columns(pl.lit(1.0).alias("historical_adjustment_factor")).select(
            ["ex_dividend_date", "amount", "historical_adjustment_factor"]
        )

    closes = ohlcv.select(
        [
            pl.col("session_end_date").alias("_d")
            if "session_end_date" in ohlcv.columns
            else pl.col("window_start").dt.date().alias("_d"),
            pl.col("close"),
        ]
    ).unique(subset=["_d"], keep="last").sort("_d")

    close_map = dict(zip(closes["_d"].to_list(), closes["close"].to_list(), strict=False))

    divs = dividends.sort("ex_dividend_date", descending=True)
    amounts = divs["amount"].to_list()
    dates = divs["ex_dividend_date"].to_list()
    factors: list[float] = []
    cum = 1.0

    sorted_close_dates = sorted(close_map.keys())

    for ex_d, amount in zip(dates, amounts, strict=False):
        prev_close = _prev_close(ex_d, close_map, sorted_close_dates)
        amt = float(amount) if amount is not None else 0.0
        if prev_close is not None and prev_close > 0 and amt > 0:
            unit = 1.0 - (amt / float(prev_close))
            if 0.0 < unit <= 1.0:
                cum *= unit
        factors.append(cum)

    out = divs.with_columns(pl.Series("historical_adjustment_factor", factors))
    return out.sort("ex_dividend_date").select(
        ["ex_dividend_date", "amount", "historical_adjustment_factor"]
    )


def _prev_close(
    ex_d: date,
    close_map: dict[date, float],
    sorted_dates: list[date],
) -> float | None:
    """Dernier close strictement avant ex_dividend_date."""
    prev: date | None = None
    for d in sorted_dates:
        if d >= ex_d:
            break
        prev = d
    if prev is None:
        return None
    return close_map.get(prev)


def _empty_ohlcv() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "window_start": pl.Datetime("ns"),
            "session_end_date": pl.Date,
            "ticker": pl.Utf8,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Int64,
        }
    )


def _empty_splits() -> pl.DataFrame:
    return pl.DataFrame(schema={"execution_date": pl.Date, "split_ratio": pl.Float64})


def _empty_dividends() -> pl.DataFrame:
    return pl.DataFrame(schema={"ex_dividend_date": pl.Date, "amount": pl.Float64})
