"""Miniatures SVG sparkline pour le dashboard chart (track 1day uniquement)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

from myquantstore.config import Settings
from myquantstore.instruments import RESOLUTION_1DAY, Instrument
from myquantstore.logging_setup import get_logger
from myquantstore.storage.aggregate_cache import aggregate_exists, read_aggregate

logger = get_logger("chart.thumbnails")

_SVG_W = 280
_SVG_H = 72
_PAD_X = 4
_PAD_Y = 6

_COLOR_UP = "#26a69a"
_COLOR_DOWN = "#ef5350"
_COLOR_FLAT = "#787b86"
_COLOR_EMPTY = "#363a45"


@dataclass(frozen=True, slots=True)
class ThumbnailSeries:
    """Série close 1day pour une miniature + perf sur la fenêtre."""

    closes: tuple[float, ...]
    performance_pct: float | None
    first_date: str | None
    last_date: str | None

    @property
    def empty(self) -> bool:
        return len(self.closes) < 2


def _aggregate_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def load_thumbnail_series(
    instrument: Instrument,
    settings: Settings,
    lookback_days: int | None = None,
) -> ThumbnailSeries:
    """Charge les closes 1day sur la fenêtre lookback (plus récents)."""
    days = lookback_days if lookback_days is not None else settings.thumbnail_lookback_days
    if not aggregate_exists(instrument, settings, resolution=RESOLUTION_1DAY):
        return ThumbnailSeries((), None, None, None)

    try:
        df = read_aggregate(instrument, settings, resolution=RESOLUTION_1DAY)
    except FileNotFoundError:
        return ThumbnailSeries((), None, None, None)

    if df.is_empty() or "close" not in df.columns or "window_start" not in df.columns:
        return ThumbnailSeries((), None, None, None)

    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
    # Harmonise tz : agrégats peuvent être Datetime[..., UTC] ou naive
    ws_dtype = df.schema["window_start"]
    if getattr(ws_dtype, "time_zone", None) is not None:
        df = df.with_columns(
            pl.col("window_start").dt.replace_time_zone(None).alias("window_start")
        )

    df = (
        df.filter(pl.col("window_start") >= pl.lit(cutoff).cast(pl.Datetime("us")))
        .select(["window_start", "close"])
        .drop_nulls()
        .sort("window_start")
    )
    if df.height < 2:
        return ThumbnailSeries((), None, None, None)

    closes = [float(c) for c in df["close"].to_list()]
    first_c, last_c = closes[0], closes[-1]
    perf: float | None = None
    if first_c != 0:
        perf = (last_c - first_c) / abs(first_c) * 100.0

    ws0 = df["window_start"][0]
    ws1 = df["window_start"][-1]
    first_date = ws0.isoformat() if hasattr(ws0, "isoformat") else str(ws0)
    last_date = ws1.isoformat() if hasattr(ws1, "isoformat") else str(ws1)
    return ThumbnailSeries(tuple(closes), perf, first_date, last_date)


def render_sparkline_svg(
    closes: tuple[float, ...] | list[float],
    *,
    width: int = _SVG_W,
    height: int = _SVG_H,
    performance_pct: float | None = None,
) -> str:
    """Génère un SVG sparkline (polyline + fill) à partir des closes."""
    if len(closes) < 2:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-label="no data">'
            f'<rect width="100%" height="100%" fill="transparent"/>'
            f'<text x="{width // 2}" y="{height // 2}" text-anchor="middle" '
            f'fill="{_COLOR_EMPTY}" font-size="11" font-family="sans-serif">n/a</text>'
            f"</svg>"
        )

    vals = [float(v) for v in closes]
    vmin = min(vals)
    vmax = max(vals)
    span = vmax - vmin
    if span <= 0:
        span = 1.0
        # ligne plate au milieu
        ymin = vmin - 0.5
        ymax = vmax + 0.5
        span = ymax - ymin
        vmin = ymin

    inner_w = width - 2 * _PAD_X
    inner_h = height - 2 * _PAD_Y
    n = len(vals)

    def _xy(i: int, v: float) -> tuple[float, float]:
        x = _PAD_X + (i / (n - 1)) * inner_w
        y = _PAD_Y + (1.0 - (v - vmin) / span) * inner_h
        return x, y

    points = [_xy(i, v) for i, v in enumerate(vals)]
    poly = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)

    # Aire sous la courbe
    area_pts = (
        f"{points[0][0]:.2f},{height - _PAD_Y:.2f} "
        + poly
        + f" {points[-1][0]:.2f},{height - _PAD_Y:.2f}"
    )

    if performance_pct is None:
        color = _COLOR_FLAT
    elif performance_pct > 0.05:
        color = _COLOR_UP
    elif performance_pct < -0.05:
        color = _COLOR_DOWN
    else:
        color = _COLOR_FLAT

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img">'
        f'<polygon points="{area_pts}" fill="{color}" fill-opacity="0.15"/>'
        f'<polyline points="{poly}" fill="none" stroke="{color}" '
        f'stroke-width="1.75" stroke-linejoin="round" stroke-linecap="round"/>'
        f"</svg>"
    )


def get_thumbnail_svg(
    instrument: Instrument,
    settings: Settings,
    lookback_days: int | None = None,
) -> str:
    """SVG thumbnail pour un instrument (1day only), avec cache mtime."""
    days = lookback_days if lookback_days is not None else settings.thumbnail_lookback_days
    path = settings.aggregate_path(instrument, resolution=RESOLUTION_1DAY)
    mtime = _aggregate_mtime(path)
    key = (instrument.key, days, mtime, str(path))
    cached = _THUMB_CACHE.get(key)
    if cached is not None:
        return cached

    series = load_thumbnail_series(instrument, settings, lookback_days=days)
    svg = render_sparkline_svg(series.closes, performance_pct=series.performance_pct)
    _THUMB_CACHE[key] = svg
    # borne simple
    if len(_THUMB_CACHE) > 512:
        # drop ~half oldest insertion order (Py3.7+ dict ordered)
        for k in list(_THUMB_CACHE.keys())[:256]:
            del _THUMB_CACHE[k]
    return svg


_THUMB_CACHE: dict[tuple[str, int, float, str], str] = {}


def resolve_display_names(
    instruments: dict[str, Instrument],
    settings: Settings,
) -> dict[str, str]:
    """Résout un nom lisible par instrument (cache tickers si dispo)."""
    names: dict[str, str] = {k: inst.symbol for k, inst in instruments.items()}

    try:
        from myquantstore.tickers.cache import TickersCache
        from myquantstore.tickers.search import strip_api_prefix

        cache = TickersCache(settings)
        if not cache.exists and not cache.legacy_all_path().exists():
            return _enrich_futures_names(names, instruments, settings)

        df = cache.read_concat(active=True)
        if df.is_empty() or "ticker" not in df.columns or "name" not in df.columns:
            return _enrich_futures_names(names, instruments, settings)

        # Map ticker nu (sans préfixe) → name (premier match)
        rows = df.select(
            pl.col("ticker").cast(pl.Utf8),
            pl.col("name").cast(pl.Utf8).fill_null(""),
            pl.col("market").cast(pl.Utf8).fill_null("") if "market" in df.columns else pl.lit(""),
        )
        by_ticker: dict[str, list[tuple[str, str]]] = {}
        for ticker, name, market in rows.iter_rows():
            bare = strip_api_prefix(str(ticker)).upper()
            by_ticker.setdefault(bare, []).append((str(name), str(market).lower()))

        type_markets = {
            "stocks": {"stocks", "otc"},
            "forex": {"fx"},
            "indices": {"indices"},
            "futures": set(),  # évite collision stock ES vs future ES
            "options": {"options"},
        }

        for key, inst in instruments.items():
            candidates = by_ticker.get(inst.symbol.upper(), [])
            preferred = type_markets.get(inst.type.value, set())
            chosen = ""
            for name, market in candidates:
                if not name:
                    continue
                if preferred and market not in preferred:
                    continue
                chosen = name
                break
            if not chosen and preferred:
                # pas de match market — ne pas prendre un nom d'un autre type
                pass
            elif not chosen and candidates:
                chosen = candidates[0][0]
            if chosen:
                names[key] = chosen
    except Exception as exc:
        logger.debug(f"resolve_display_names tickers: {exc}")

    return _enrich_futures_names(names, instruments, settings)


def _enrich_futures_names(
    names: dict[str, str],
    instruments: dict[str, Instrument],
    settings: Settings,
) -> dict[str, str]:
    """Complète les noms futures via le cache contrats si le name == symbol."""
    from myquantstore.instruments import InstrumentType

    for key, inst in instruments.items():
        if inst.type != InstrumentType.FUTURES:
            continue
        if names.get(key) and names[key] != inst.symbol:
            continue
        try:
            path = settings.contracts_cache_path(inst.symbol)
            if not path.exists():
                continue
            from myquantstore.storage.parquet_io import read_parquet

            df = read_parquet(path)
            if "name" not in df.columns or df.is_empty():
                continue
            # Cherche un name qui n'est pas juste le ticker contrat
            for nm in df["name"].drop_nulls().to_list():
                s = str(nm).strip()
                # souvent "ESZ5 Future"
                if (
                    s
                    and s.upper() != inst.symbol.upper()
                    and s.endswith(" Future")
                ):
                    names[key] = f"{inst.symbol} Future"
                    break
            if names.get(key) == inst.symbol:
                names[key] = f"{inst.symbol} Continuous"
        except Exception:
            pass
    return names


def build_dashboard_cards(
    instruments: dict[str, Instrument],
    settings: Settings,
    lookback_days: int | None = None,
) -> list[dict[str, object]]:
    """Métadonnées des cartes dashboard (tri côté client)."""
    days = lookback_days if lookback_days is not None else settings.thumbnail_lookback_days
    display_names = resolve_display_names(instruments, settings)
    cards: list[dict[str, object]] = []

    for key, inst in instruments.items():
        series = load_thumbnail_series(inst, settings, lookback_days=days)
        cards.append(
            {
                "key": key,
                "type": inst.type.value,
                "symbol": inst.symbol,
                "name": display_names.get(key, inst.symbol),
                "performance_pct": series.performance_pct,
                "has_1day": not series.empty,
                "first_date": series.first_date,
                "last_date": series.last_date,
                "thumbnail_url": f"/api/thumbnail/{key}.svg?lookback_days={days}",
            }
        )
    return cards


def empty_placeholder_svg() -> str:
    """SVG placeholder (pas de données 1day)."""
    return render_sparkline_svg(())
