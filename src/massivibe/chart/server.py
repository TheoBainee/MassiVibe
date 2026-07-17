"""Serveur web FastAPI pour la visualisation interactive des chandeliers.

Backend de la commande ``massivibe chart``. Expose :

- ``GET /`` : redirect vers l'instrument par défaut.
- ``GET /{instrument_key}`` : page HTML du chart (template unique).
- ``GET /static/{file}`` : fichiers statiques (lightweight-charts JS, apache-arrow JS).
- ``GET /api/candles`` : chandeliers OHLCV en Arrow IPC (binaire).
- ``GET /api/meta`` : métadonnées JSON (tick_size, date range).

**Multi-type** : les instruments sont indexés par leur clé ``"{type}:{symbol}"``
(ex: ``futures:ES``, ``stocks:AAPL``) pour éviter les collisions de symboles
entre types. Le paramètre ``product`` des endpoints API = cette clé.

**Buffer progressif** : le frontend charge initialement
``buffer_multiplier × max_visible_candles`` chandeliers (les plus récents), puis
fetch des chunks plus anciens au fil du pan vers la gauche (lazy loading).

**Format de transfert** : Arrow IPC (binaire). Polars ``write_ipc()`` côté
serveur, ``apache-arrow`` JS côté frontend.

**License TradingView** : Lightweight Charts est sous Apache-2.0 avec
attribution requise (voir fichier ``NOTICE`` dans ce module).
"""

from __future__ import annotations

from datetime import datetime, time
from io import BytesIO
from pathlib import Path
from typing import Any

import polars as pl
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from massivibe.chains import InstrumentChain
from massivibe.config import Settings
from massivibe.instruments import Instrument, InstrumentType
from massivibe.logging_setup import get_logger
from massivibe.query.reader import query

logger = get_logger("chart.server")

_STATIC_DIR = Path(__file__).parent / "static"


def create_chart_app(
    settings: Settings,
    instruments: dict[str, Instrument],
    chains: dict[str, InstrumentChain],
    defaults: ChartDefaults,
) -> FastAPI:
    """Crée l'application FastAPI pour le serveur de visualisation.

    :param settings: Configuration globale.
    :param instruments: Dictionnaire {instrument_key: Instrument} servis.
    :param chains: Dictionnaire {instrument_key: InstrumentChain}.
    :param defaults: Paramètres par défaut injectés dans le frontend.
    :return: Application FastAPI prête à lancer avec uvicorn.
    """
    app = FastAPI(title="MassiVibe Chart", docs_url="/docs")
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/", response_class=RedirectResponse)
    async def index() -> str:
        return f"/{defaults.default_product}"

    @app.get("/{instrument_key}", response_class=HTMLResponse)
    async def chart_page(instrument_key: str) -> HTMLResponse:
        if instrument_key not in instruments:
            raise HTTPException(status_code=404, detail=f"Instrument '{instrument_key}' non configuré")
        html = _render_chart_html(instrument_key, defaults)
        return HTMLResponse(content=html)

    @app.get("/api/candles")
    async def get_candles(
        product: str = Query(..., description="Clé instrument (ex: futures:ES)"),
        timescale_unit: str = Query("min", description="Unité de l'UT: min ou hour"),
        timescale_nb: int = Query(1, ge=1, description="Nombre d'unités"),
        limit: int = Query(
            settings.max_visible_candles * settings.buffer_multiplier,
            ge=1,
            description="Nombre max de chandeliers à retourner",
        ),
        before: str | None = Query(None, description="Chandeliers avant cette date (ISO 8601)"),
    ) -> Response:
        if product not in instruments:
            raise HTTPException(status_code=404, detail=f"Instrument '{product}' non configuré")

        before_dt: datetime | None = None
        if before:
            try:
                parsed = datetime.fromisoformat(before.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    from datetime import UTC

                    parsed = parsed.replace(tzinfo=UTC)
                before_dt = parsed
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Format 'before' invalide: {before}") from None

        k_minutes = _timescale_to_k_minutes(timescale_unit, timescale_nb)

        instrument = instruments[product]
        chain = chains.get(product)

        df = query(
            instrument,
            settings,
            chain,
            end=before_dt,
            k_minutes=k_minutes,
            intraday_begin=defaults.intraday_begin,
            intraday_end=defaults.intraday_end,
            normalize_tick_size=defaults.normalize_tick_size,
            adjust_rollover=defaults.adjust_rollover,
            no_split=defaults.no_split,
            limit=None,
        )

        if df.is_empty():
            return Response(content=b"", media_type="application/octet-stream")

        if limit is not None and limit > 0:
            df = df.tail(limit)

        chart_df = _prepare_chart_df(df)
        buffer = BytesIO()
        chart_df.write_ipc(buffer)
        logger.debug(
            f"API /candles: product={product} k={k_minutes}min limit={limit} "
            f"before={before} -> {chart_df.height} candles, {len(buffer.getvalue())} bytes"
        )
        return Response(content=buffer.getvalue(), media_type="application/octet-stream")

    @app.get("/api/meta")
    async def get_meta(product: str = Query(...)) -> dict[str, Any]:
        if product not in instruments:
            raise HTTPException(status_code=404, detail=f"Instrument '{product}' non configuré")

        instrument = instruments[product]
        chain = chains.get(product)

        # tick_size : uniquement pertinent pour futures (via RolloverChain)
        tick_size: float | None = None
        if chain is not None and instrument.type == InstrumentType.FUTURES:
            from datetime import UTC
            from datetime import datetime as _dt

            active_ticker = chain.active_contract(_dt.now(UTC).date())
            if active_ticker:
                tick_size = chain.tick_size_for_ticker(active_ticker)

        from massivibe.storage.aggregate_cache import read_aggregate

        try:
            df = read_aggregate(instrument, settings)
        except FileNotFoundError:
            return {"product": product, "tick_size": tick_size, "first_date": None, "last_date": None}

        if df.is_empty():
            return {"product": product, "tick_size": tick_size, "first_date": None, "last_date": None}

        from datetime import datetime as _dt2

        ws_min = df["window_start"].min()
        ws_max = df["window_start"].max()
        first_date = ws_min.isoformat() if isinstance(ws_min, _dt2) else None
        last_date = ws_max.isoformat() if isinstance(ws_max, _dt2) else None

        return {
            "product": product,
            "tick_size": tick_size,
            "first_date": first_date,
            "last_date": last_date,
            "total_candles": df.height,
        }

    return app


class ChartDefaults:
    """Paramètres par défaut injectés dans le frontend (page HTML)."""

    def __init__(
        self,
        default_product: str,
        timescale_unit: str = "min",
        timescale_nb: int = 1,
        nb_candle: int = 50000,
        max_visible_candles: int = 50000,
        buffer_multiplier: int = 3,
        fetch_chunk_size: int = 50000,
        intraday_begin: time | None = None,
        intraday_end: time | None = None,
        normalize_tick_size: bool = False,
        adjust_rollover: bool = False,
        no_split: bool = False,
    ) -> None:
        self.default_product = default_product
        self.timescale_unit = timescale_unit
        self.timescale_nb = timescale_nb
        self.nb_candle = nb_candle
        self.max_visible_candles = max_visible_candles
        self.buffer_multiplier = buffer_multiplier
        self.fetch_chunk_size = fetch_chunk_size
        self.intraday_begin = intraday_begin
        self.intraday_end = intraday_end
        self.normalize_tick_size = normalize_tick_size
        self.adjust_rollover = adjust_rollover
        self.no_split = no_split


def _timescale_to_k_minutes(unit: str, nb: int) -> int:
    """Convertit (unit, nb) en k_minutes pour resample_ohlcv()."""
    if unit == "min":
        return nb
    elif unit == "hour":
        return nb * 60
    else:
        raise HTTPException(
            status_code=400,
            detail=f"timescale_unit '{unit}' non implémenté. Unités supportées: min, hour.",
        )


def _prepare_chart_df(df: pl.DataFrame) -> pl.DataFrame:
    """Filtre et caste les colonnes pour produire un Arrow IPC compatible apache-arrow JS.

    Le frontend n'a besoin que de : time, OHLC, volume, candle_count.
    On élimine les colonnes ``Categorical`` (non supportées par apache-arrow JS)
    et on caste les timestamps en ``ms`` + le volume en ``Int32``.
    """
    time_col = "bucket_start" if "bucket_start" in df.columns else "window_start"

    select_exprs: list[pl.Expr] = [
        pl.col(time_col).cast(pl.Datetime("ms")).alias("time"),
        pl.col("open").cast(pl.Float64),
        pl.col("high").cast(pl.Float64),
        pl.col("low").cast(pl.Float64),
        pl.col("close").cast(pl.Float64),
    ]

    if "volume" in df.columns:
        select_exprs.append(pl.col("volume").cast(pl.Int32))
    if "candle_count" in df.columns:
        select_exprs.append(pl.col("candle_count").cast(pl.Int32))

    chart_df = df.select(select_exprs)

    # Dédupliquer sur le timestamp (dates de rollover futures : deux contrats au même window_start)
    chart_df = chart_df.unique(subset=["time"], keep="last").sort("time")
    return chart_df


def _render_chart_html(instrument_key: str, defaults: ChartDefaults) -> str:
    """Génère la page HTML du chart en injectant les paramètres par défaut."""
    template_path = _STATIC_DIR / "chart.html"
    html = template_path.read_text(encoding="utf-8")

    intraday_begin_str = f'"{defaults.intraday_begin.isoformat()}"' if defaults.intraday_begin else "null"
    intraday_end_str = f'"{defaults.intraday_end.isoformat()}"' if defaults.intraday_end else "null"

    replacements = {
        "__PRODUCT__": instrument_key,
        "__TIMESCALE_UNIT__": defaults.timescale_unit,
        "__TIMESCALE_NB__": str(defaults.timescale_nb),
        "__NB_CANDLE__": str(defaults.nb_candle),
        "__MAX_VISIBLE_CANDLES__": str(defaults.max_visible_candles),
        "__BUFFER_MULTIPLIER__": str(defaults.buffer_multiplier),
        "__FETCH_CHUNK_SIZE__": str(defaults.fetch_chunk_size),
        "__INTRADAY_BEGIN__": intraday_begin_str,
        "__INTRADAY_END__": intraday_end_str,
        "__NORMALIZE_TICK_SIZE__": str(defaults.normalize_tick_size).lower(),
    }

    for key, value in replacements.items():
        html = html.replace(key, value)

    current_ts = f"{defaults.timescale_unit}:{defaults.timescale_nb}"
    ts_options = ["min:1", "min:7", "min:15", "min:30", "min:60", "hour:1", "hour:2", "hour:4"]
    for opt in ts_options:
        marker = f"__SEL_{opt.replace(':', '')}__"
        html = html.replace(marker, "selected" if opt == current_ts else "")

    return html


def run_server(
    settings: Settings,
    instruments: dict[str, Instrument],
    chains: dict[str, InstrumentChain],
    defaults: ChartDefaults,
    port: int,
    host: str,
    mdns: bool = False,
) -> None:
    """Lance le serveur uvicorn (bloquant)."""
    import uvicorn

    app = create_chart_app(settings, instruments, chains, defaults)

    mdns_service = None
    if mdns:
        from massivibe.chart.mdns import register_mdns

        mdns_service = register_mdns(host, port)
        logger.info("Service mDNS enregistré: accessible via le réseau local")

    logger.info(f"Serveur chart démarré sur http://{host}:{port}")
    logger.info(f"Instruments servis: {list(instruments.keys())}")
    logger.info(f"Instrument par défaut: {defaults.default_product}")
    logger.info(
        f"Timescale: {defaults.timescale_nb}{defaults.timescale_unit} | "
        f"Max visible: {defaults.max_visible_candles} | "
        f"Buffer: {defaults.buffer_multiplier}x"
    )

    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    finally:
        if mdns_service:
            mdns_service.close()
            logger.info("Service mDNS désenregistré")
