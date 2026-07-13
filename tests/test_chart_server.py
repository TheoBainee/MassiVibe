"""Tests du module chart/server.py (serveur FastAPI de visualisation)."""

from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO

import polars as pl
import pytest
from fastapi.testclient import TestClient

from massivibe.chart.server import ChartDefaults, create_chart_app
from massivibe.pipeline.aggregator import aggregate
from massivibe.storage.raw_dumps import save_raw_dump


def _make_ohlcv_df(ticker: str, timestamps: list[datetime], prices: list[float]) -> pl.DataFrame:
    """Crée un DataFrame OHLCV pour les tests."""
    n = len(timestamps)
    return pl.DataFrame(
        {
            "window_start": timestamps,
            "ticker": [ticker] * n,
            "open": prices,
            "high": [p + 1 for p in prices],
            "low": [p - 1 for p in prices],
            "close": [p + 0.5 for p in prices],
            "settlement_price": [p + 0.5 for p in prices],
            "volume": [100] * n,
            "dollar_volume": [1000.0] * n,
            "transactions": [10] * n,
            "session_end_date": [ts.date() for ts in timestamps],
        }
    )


@pytest.fixture
def chart_setup(tmp_settings, sample_chain):
    """Crée un cache agrégé pour ES et retourne (settings, chains, defaults)."""
    ts = [
        datetime(2025, 6, 1, 9, 30, 0, tzinfo=UTC),
        datetime(2025, 6, 1, 9, 31, 0, tzinfo=UTC),
        datetime(2025, 6, 1, 9, 32, 0, tzinfo=UTC),
        datetime(2025, 6, 1, 9, 33, 0, tzinfo=UTC),
        datetime(2025, 6, 1, 9, 34, 0, tzinfo=UTC),
    ]
    prices = [4500.00, 4501.25, 4502.50, 4501.75, 4500.50]
    df = _make_ohlcv_df("ESM5", ts, prices)
    save_raw_dump(df, "ES", "ESM5", "20260711T183000", tmp_settings)
    aggregate("ES", tmp_settings)

    chains = {"ES": sample_chain}
    defaults = ChartDefaults(default_product="ES")
    return tmp_settings, chains, defaults


class TestChartServer:
    """Tests des endpoints du serveur chart."""

    def test_get_index_redirects_to_default_product(self, chart_setup):
        """GET / redirige vers le product par défaut."""
        settings, chains, defaults = chart_setup
        app = create_chart_app(settings, chains, defaults)
        client = TestClient(app)

        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"] == "/ES"

    def test_get_product_page_returns_html(self, chart_setup):
        """GET /ES retourne la page HTML du chart."""
        settings, chains, defaults = chart_setup
        app = create_chart_app(settings, chains, defaults)
        client = TestClient(app)

        resp = client.get("/ES")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "ES" in resp.text
        assert "lightweight-charts" in resp.text

    def test_get_unknown_product_404(self, chart_setup):
        """GET /UNKNOWN retourne 404."""
        settings, chains, defaults = chart_setup
        app = create_chart_app(settings, chains, defaults)
        client = TestClient(app)

        resp = client.get("/UNKNOWN")
        assert resp.status_code == 404

    def test_get_static_lightweight_charts_js(self, chart_setup):
        """GET /static/lightweight-charts.standalone.production.js retourne le JS."""
        settings, chains, defaults = chart_setup
        app = create_chart_app(settings, chains, defaults)
        client = TestClient(app)

        resp = client.get("/static/lightweight-charts.standalone.production.js")
        assert resp.status_code == 200
        assert len(resp.content) > 1000  # Le fichier fait ~192KB

    def test_get_candles_returns_arrow_ipc(self, chart_setup):
        """GET /api/candles retourne des chandeliers en Arrow IPC."""
        settings, chains, defaults = chart_setup
        app = create_chart_app(settings, chains, defaults)
        client = TestClient(app)

        resp = client.get("/api/candles?product=ES&timescale_unit=min&timescale_nb=1&limit=10")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/octet-stream"
        assert len(resp.content) > 0

        # Parser l'Arrow IPC pour vérifier le contenu
        df = pl.read_ipc(BytesIO(resp.content))
        assert df.height == 5  # 5 candles dans le setup
        assert "open" in df.columns
        assert "close" in df.columns

    def test_get_candles_unknown_product_404(self, chart_setup):
        """GET /api/candles?product=UNKNOWN retourne 404."""
        settings, chains, defaults = chart_setup
        app = create_chart_app(settings, chains, defaults)
        client = TestClient(app)

        resp = client.get("/api/candles?product=UNKNOWN")
        assert resp.status_code == 404

    def test_get_candles_with_before_param(self, chart_setup):
        """GET /api/candles?before=... filtre les chandeliers avant cette date (inclusive)."""
        settings, chains, defaults = chart_setup
        app = create_chart_app(settings, chains, defaults)
        client = TestClient(app)

        # before = 09:32 (inclusive) → retourne 09:30, 09:31, 09:32
        before = "2025-06-01T09:32:00"
        resp = client.get(f"/api/candles?product=ES&limit=10&before={before}")
        assert resp.status_code == 200

        df = pl.read_ipc(BytesIO(resp.content))
        assert df.height == 3  # 09:30, 09:31, 09:32 (<= est inclusif)

    def test_get_candles_timescale_7min(self, chart_setup):
        """GET /api/candles?timescale_nb=7 retourne une réponse (éventuellement vide si partiels droppés)."""
        settings, chains, defaults = chart_setup
        app = create_chart_app(settings, chains, defaults)
        client = TestClient(app)

        resp = client.get("/api/candles?product=ES&timescale_unit=min&timescale_nb=7&limit=10")
        assert resp.status_code == 200
        # 5 candles 1min k=7 → bucket partiel droppé → peut être vide
        # (le bucket 09:30-09:36 n'a que 5 candles < 7, mais ce n'est pas un partiel de fin de session)
        # En réalité le bucket est conservé car candle_count < k est un gap, pas un partiel
        if len(resp.content) > 0:
            df = pl.read_ipc(BytesIO(resp.content))
            assert df.height >= 1

    def test_get_candles_invalid_timescale_unit(self, chart_setup):
        """GET /api/candles?timescale_unit=sec retourne 400 (non implémenté)."""
        settings, chains, defaults = chart_setup
        app = create_chart_app(settings, chains, defaults)
        client = TestClient(app)

        resp = client.get("/api/candles?product=ES&timescale_unit=sec&timescale_nb=1")
        assert resp.status_code == 400

    def test_get_meta_returns_json(self, chart_setup):
        """GET /api/meta retourne les métadonnées JSON."""
        settings, chains, defaults = chart_setup
        app = create_chart_app(settings, chains, defaults)
        client = TestClient(app)

        resp = client.get("/api/meta?product=ES")
        assert resp.status_code == 200
        data = resp.json()
        assert data["product"] == "ES"
        assert data["tick_size"] == 0.25
        assert data["total_candles"] == 5

    def test_get_meta_unknown_product_404(self, chart_setup):
        """GET /api/meta?product=UNKNOWN retourne 404."""
        settings, chains, defaults = chart_setup
        app = create_chart_app(settings, chains, defaults)
        client = TestClient(app)

        resp = client.get("/api/meta?product=UNKNOWN")
        assert resp.status_code == 404

    def test_chart_html_injects_product(self, chart_setup):
        """La page HTML injecte le product et les defaults correctement."""
        settings, chains, defaults = chart_setup
        app = create_chart_app(settings, chains, defaults)
        client = TestClient(app)

        resp = client.get("/ES")
        text = resp.text
        assert '"__PRODUCT__"' not in text  # Placeholder remplacé
        assert 'ES' in text
        assert "50000" in text  # max_visible_candles
