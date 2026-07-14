"""Serveur web FastAPI pour la visualisation interactive des chandeliers.

Ce module implémente le serveur backend de la commande ``massivibe chart``.
Il expose :

- ``GET /`` : redirect vers le product par défaut.
- ``GET /{product}`` : page HTML du chart (template unique, product injecté en JS).
- ``GET /static/{file}`` : fichiers statiques (lightweight-charts JS, apache-arrow JS).
- ``GET /api/candles`` : chandeliers OHLCV en Arrow IPC (binaire).
- ``GET /api/meta`` : métadonnées JSON (tick_size, date range).

**Buffer progressif** : le frontend charge initialement ``buffer_multiplier ×
max_visible_candles`` chandeliers (les plus récents), puis fetch des chunks
plus anciens au fur et à mesure du pan vers la gauche (lazy loading horizontal).
Le cap de zoom ``max_visible_candles`` empêche l'utilisateur de dézoomer au-delà.

**Format de transfert** : Arrow IPC (binaire). Polars ``write_ipc()`` côté serveur,
``apache-arrow`` JS côté frontend. ~3x plus compact et rapide à parser que JSON.

**License TradingView** : Lightweight Charts est sous Apache-2.0 avec attribution
requise. Le logo TradingView est affiché sur le chart via ``attributionLogo: true``
(voir fichier ``NOTICE`` dans ce module).
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

from massivibe.config import Settings
from massivibe.contracts.rollover import RolloverChain
from massivibe.logging_setup import get_logger
from massivibe.query.reader import query

logger = get_logger("chart.server")

# Répertoire des fichiers statiques (JS embarqués + template HTML)
_STATIC_DIR = Path(__file__).parent / "static"


def create_chart_app(
    settings: Settings,
    chains: dict[str, RolloverChain],
    defaults: ChartDefaults,
) -> FastAPI:
    """Crée l'application FastAPI pour le serveur de visualisation.

    :param settings: Configuration globale.
    :param chains: Dictionnaire {product_code: RolloverChain} pour les products servis.
    :param defaults: Paramètres par défaut (timescale, intraday, normalize, etc.) injectés dans le frontend.
    :return: Application FastAPI prête à lancer avec uvicorn.
    """
    app = FastAPI(title="MassiVibe Chart", docs_url="/docs")

    # Monter les fichiers statiques (JS embarqués)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # --- GET / : redirect vers le product par défaut ---
    @app.get("/", response_class=RedirectResponse)
    async def index() -> str:
        return f"/{defaults.default_product}"

    # --- GET /{product} : page HTML du chart ---
    @app.get("/{product}", response_class=HTMLResponse)
    async def chart_page(product: str) -> HTMLResponse:
        if product not in chains:
            raise HTTPException(status_code=404, detail=f"Product '{product}' non configuré")
        html = _render_chart_html(product, defaults)
        return HTMLResponse(content=html)

    # --- GET /api/candles : chandeliers en Arrow IPC ---
    @app.get("/api/candles")
    async def get_candles(
        product: str = Query(..., description="Code produit (ex: NQ)"),
        timescale_unit: str = Query("min", description="Unité de l'UT: min ou hour"),
        timescale_nb: int = Query(1, ge=1, description="Nombre d'unités"),
        limit: int = Query(
            settings.max_visible_candles * settings.buffer_multiplier,
            ge=1,
            description="Nombre max de chandeliers à retourner",
        ),
        before: str | None = Query(None, description="Retourne les chandeliers avant cette date (ISO 8601)"),
    ) -> Response:
        if product not in chains:
            raise HTTPException(status_code=404, detail=f"Product '{product}' non configuré")

        # Parser before (string ISO → datetime)
        # La normalisation timezone est gérée dans query() (reader.py)
        before_dt: datetime | None = None
        if before:
            try:
                parsed = datetime.fromisoformat(before.replace("Z", "+00:00"))
                # Assurer timezone-aware (UTC par défaut si pas de tz)
                if parsed.tzinfo is None:
                    from datetime import UTC

                    parsed = parsed.replace(tzinfo=UTC)
                before_dt = parsed
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Format 'before' invalide: {before}") from None

        # Calculer k_minutes depuis timescale_unit + timescale_nb
        k_minutes = _timescale_to_k_minutes(timescale_unit, timescale_nb)

        # Query : réutilise la fonction query() existante
        # On passe limit=None car query() fait df.head(limit) qui retourne les
        # PLUS ANCIENNES candles. Le chart veut les plus RÉCENTES → on fait tail()
        # après coup.
        df = query(
            product,
            settings,
            chains[product],
            end=before_dt,
            k_minutes=k_minutes,
            intraday_begin=defaults.intraday_begin,
            intraday_end=defaults.intraday_end,
            normalize_tick_size=defaults.normalize_tick_size,
            adjust_rollover=defaults.adjust_rollover,
            limit=None,
        )

        if df.is_empty():
            return Response(content=b"", media_type="application/octet-stream")

        # Prendre les `limit` candles les plus récentes (tail = fin du DataFrame)
        if limit is not None and limit > 0:
            df = df.tail(limit)

        # Filtrer les colonnes utiles au chart + caster en types Arrow simples
        # (évite string_view des Categorical que apache-arrow JS ne supporte pas)
        chart_df = _prepare_chart_df(df)

        # Sérialiser en Arrow IPC
        buffer = BytesIO()
        chart_df.write_ipc(buffer)
        logger.debug(
            f"API /candles: product={product} k={k_minutes}min limit={limit} "
            f"before={before} -> {chart_df.height} candles, {len(buffer.getvalue())} bytes"
        )
        return Response(content=buffer.getvalue(), media_type="application/octet-stream")

    # --- GET /api/meta : métadonnées JSON ---
    @app.get("/api/meta")
    async def get_meta(product: str = Query(...)) -> dict[str, Any]:
        if product not in chains:
            raise HTTPException(status_code=404, detail=f"Product '{product}' non configuré")

        chain = chains[product]
        # Récupérer le tick_size du contrat actif (le plus récent)
        from datetime import UTC, datetime

        active_ticker = chain.active_contract(datetime.now(UTC).date())
        tick_size = chain.tick_size_for_ticker(active_ticker) if active_ticker else None

        # Date range depuis l'agrégé
        from massivibe.storage.aggregate_cache import read_aggregate

        df = read_aggregate(product, settings)
        if df.is_empty():
            return {"product": product, "tick_size": tick_size, "first_date": None, "last_date": None}

        return {
            "product": product,
            "tick_size": tick_size,
            "first_date": df["window_start"].min().isoformat(),
            "last_date": df["window_start"].max().isoformat(),
            "total_candles": df.height,
        }

    return app


class ChartDefaults:
    """Paramètres par défaut injectés dans le frontend (page HTML).

    Ces paramètres sont set au lancement via CLI — le frontend n'a pas besoin
    de les passer dans l'API. Pour changer, relancer le serveur.
    """

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


def _timescale_to_k_minutes(unit: str, nb: int) -> int:
    """Convertit (unit, nb) en k_minutes pour resample_ohlcv().

    :raises HTTPException: Si l'unité n'est pas supportée.
    """
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
    """Filtre et caste les colonnes pour produire un Arrow IPC compatible avec apache-arrow JS.

    Le frontend chart n'a besoin que de : time, OHLC, volume, candle_count.
    On élimine les colonnes ``Categorical`` (ticker, run_id, product_code) car Polars
    les encode en ``dictionary<values=string_view>`` qui n'est pas supporté par
    apache-arrow JS 17.0.0 ( erreur "Unrecognized type: undefined (24)" ).

    On caste aussi ``window_start``/``bucket_start`` en millisecondes (``ms``) car
    apache-arrow JS 17.0.0 supporte ``timestamp[ms]`` de manière plus fiable que
    ``timestamp[us]`` (microsecondes). Le volume est casté en ``Int32`` car
    apache-arrow JS retourne des ``BigInt`` pour les ``Int64``, que Lightweight
    Charts n'accepte pas pour les valeurs numériques.

    :param df: DataFrame Polars issu de ``query()``.
    :return: DataFrame restreint aux colonnes du chart, en types Arrow simples.
    """
    # Colonne time : bucket_start (resamplé) ou window_start (1min)
    time_col = "bucket_start" if "bucket_start" in df.columns else "window_start"

    # Construire la liste des colonnes à sélectionner
    select_exprs: list[pl.Expr] = [
        pl.col(time_col).cast(pl.Datetime("ms")).alias("time"),
        pl.col("open").cast(pl.Float64),
        pl.col("high").cast(pl.Float64),
        pl.col("low").cast(pl.Float64),
        pl.col("close").cast(pl.Float64),
    ]

    # Volume (optionnel — peut être absent si normalize_tick_size)
    # Int32 (pas Int64) car apache-arrow JS retourne BigInt pour Int64,
    # que Lightweight Charts n'accepte pas. Les volumes < 2^31 de toute façon.
    if "volume" in df.columns:
        select_exprs.append(pl.col("volume").cast(pl.Int32))
    # candle_count (présent si resamplé k > 1)
    if "candle_count" in df.columns:
        select_exprs.append(pl.col("candle_count").cast(pl.Int32))

    chart_df = df.select(select_exprs)

    # Dédupliquer sur le timestamp : sur les dates de rollover, l'ancien et le
    # nouveau contrat ont tous deux des candles au même window_start. L'aggregator
    # déduplique sur (window_start, ticker) — pas sur window_start seul — donc ces
    # doublons subsistent. Lightweight Charts exige des timestamps uniques dans
    # setData() (sinon "Value is null"). Le resampling k>1 fusionne naturellement
    # ces doublons via group_by, donc le problème ne se produit qu'en 1min.
    chart_df = chart_df.unique(subset=["time"], keep="last").sort("time")

    return chart_df


def _render_chart_html(product: str, defaults: ChartDefaults) -> str:
    """Génère la page HTML du chart en injectant les paramètres par défaut.

    Le template HTML est lu depuis ``static/chart.html`` et les variables JS
    sont injectées par string replacement.
    """
    template_path = _STATIC_DIR / "chart.html"
    html = template_path.read_text(encoding="utf-8")

    # Injecter les variables dans le template
    # Les valeurs intraday sont des strings "HH:MM:SS" — doivent être quotées en JS
    # (sinon "04:00:00" est parsé comme du JS invalide et casse tout le bloc <script>).
    intraday_begin_str = f'"{defaults.intraday_begin.isoformat()}"' if defaults.intraday_begin else "null"
    intraday_end_str = f'"{defaults.intraday_end.isoformat()}"' if defaults.intraday_end else "null"

    replacements = {
        "__PRODUCT__": product,
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

    # Présélectionner l'UT dans le dropdown
    current_ts = f"{defaults.timescale_unit}:{defaults.timescale_nb}"
    ts_options = ["min:1", "min:7", "min:15", "min:30", "min:60", "hour:1", "hour:2", "hour:4"]
    for opt in ts_options:
        marker = f"__SEL_{opt.replace(':', '')}__"
        html = html.replace(marker, "selected" if opt == current_ts else "")

    return html


def run_server(
    settings: Settings,
    chains: dict[str, RolloverChain],
    defaults: ChartDefaults,
    port: int,
    host: str,
    mdns: bool = False,
) -> None:
    """Lance le serveur uvicorn (bloquant).

    :param settings: Configuration globale.
    :param chains: Dictionnaire {product_code: RolloverChain}.
    :param defaults: Paramètres par défaut du chart.
    :param port: Port d'écoute.
    :param host: Host bind.
    :param mdns: Si True, enregistre le service via mDNS (zeroconf).
    """
    import uvicorn

    app = create_chart_app(settings, chains, defaults)

    # mDNS (optionnel)
    mdns_service = None
    if mdns:
        from massivibe.chart.mdns import register_mdns

        mdns_service = register_mdns(host, port)
        logger.info("Service mDNS enregistré: accessible via le réseau local")

    logger.info(f"Serveur chart démarré sur http://{host}:{port}")
    logger.info(f"Products servis: {list(chains.keys())}")
    logger.info(f"Product par défaut: {defaults.default_product}")
    logger.info(
        f"Timescale: {defaults.timescale_nb}{defaults.timescale_unit} | "
        f"Max visible: {defaults.max_visible_candles} | "
        f"Buffer: {defaults.buffer_multiplier}x"
    )

    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    finally:
        if mdns_service:
            mdns_service.unregister()
            logger.info("Service mDNS désenregistré")
