"""Tests du module chart/server.py (serveur FastAPI de visualisation)."""

from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO

import polars as pl
import pytest
from fastapi.testclient import TestClient

from myquantstore.chart.server import ChartDefaults, create_chart_app
from myquantstore.pipeline.aggregator import aggregate
from myquantstore.storage.raw_dumps import save_raw_dump


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
def chart_setup(tmp_settings, es_instrument, sample_chain):
    """Crée un cache agrégé pour ES et retourne (settings, instruments, chains, defaults)."""
    ts = [
        datetime(2025, 6, 1, 9, 30, 0, tzinfo=UTC),
        datetime(2025, 6, 1, 9, 31, 0, tzinfo=UTC),
        datetime(2025, 6, 1, 9, 32, 0, tzinfo=UTC),
        datetime(2025, 6, 1, 9, 33, 0, tzinfo=UTC),
        datetime(2025, 6, 1, 9, 34, 0, tzinfo=UTC),
    ]
    prices = [4500.00, 4501.25, 4502.50, 4501.75, 4500.50]
    df = _make_ohlcv_df("ESM5", ts, prices)
    save_raw_dump(df, es_instrument, "ESM5", "20260711T183000", tmp_settings)
    aggregate(es_instrument, tmp_settings)

    key = es_instrument.key  # "futures:ES"
    instruments = {key: es_instrument}
    chains = {key: sample_chain}
    defaults = ChartDefaults(default_product=key)
    return tmp_settings, instruments, chains, defaults


class TestChartServer:
    """Tests des endpoints du serveur chart."""

    def test_get_index_redirects_to_default(self, chart_setup):
        """GET / redirige vers l'instrument par défaut."""
        settings, instruments, chains, defaults = chart_setup
        app = create_chart_app(settings, instruments, chains, defaults)
        client = TestClient(app)

        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"] == "/futures:ES"

    def test_get_instrument_page_returns_html(self, chart_setup):
        """GET /futures:ES retourne la page HTML du chart."""
        settings, instruments, chains, defaults = chart_setup
        app = create_chart_app(settings, instruments, chains, defaults)
        client = TestClient(app)

        resp = client.get("/futures:ES")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "futures:ES" in resp.text
        assert "lightweight-charts" in resp.text

    def test_get_unknown_instrument_404(self, chart_setup):
        settings, instruments, chains, defaults = chart_setup
        app = create_chart_app(settings, instruments, chains, defaults)
        client = TestClient(app)

        resp = client.get("/UNKNOWN")
        assert resp.status_code == 404

    def test_get_static_lightweight_charts_js(self, chart_setup):
        settings, instruments, chains, defaults = chart_setup
        app = create_chart_app(settings, instruments, chains, defaults)
        client = TestClient(app)

        resp = client.get("/static/lightweight-charts.standalone.production.js")
        assert resp.status_code == 200
        assert len(resp.content) > 1000

    def test_get_candles_returns_arrow_ipc(self, chart_setup):
        """GET /api/candles retourne des chandeliers en Arrow IPC."""
        settings, instruments, chains, defaults = chart_setup
        app = create_chart_app(settings, instruments, chains, defaults)
        client = TestClient(app)

        resp = client.get("/api/candles?product=futures:ES&timescale_unit=min&timescale_nb=1&limit=10")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/octet-stream"
        assert len(resp.content) > 0

        df = pl.read_ipc(BytesIO(resp.content))
        assert df.height == 5
        assert "open" in df.columns
        assert "close" in df.columns

    def test_get_candles_unknown_product_404(self, chart_setup):
        settings, instruments, chains, defaults = chart_setup
        app = create_chart_app(settings, instruments, chains, defaults)
        client = TestClient(app)

        resp = client.get("/api/candles?product=UNKNOWN")
        assert resp.status_code == 404

    def test_get_candles_with_before_param(self, chart_setup):
        settings, instruments, chains, defaults = chart_setup
        app = create_chart_app(settings, instruments, chains, defaults)
        client = TestClient(app)

        before = "2025-06-01T09:32:00"
        resp = client.get(f"/api/candles?product=futures:ES&limit=10&before={before}")
        assert resp.status_code == 200

        df = pl.read_ipc(BytesIO(resp.content))
        assert df.height == 3  # 09:30, 09:31, 09:32

    def test_get_candles_timescale_7min(self, chart_setup):
        settings, instruments, chains, defaults = chart_setup
        app = create_chart_app(settings, instruments, chains, defaults)
        client = TestClient(app)

        resp = client.get("/api/candles?product=futures:ES&timescale_unit=min&timescale_nb=7&limit=10")
        assert resp.status_code == 200
        if len(resp.content) > 0:
            df = pl.read_ipc(BytesIO(resp.content))
            assert df.height >= 1

    def test_get_candles_invalid_timescale_unit(self, chart_setup):
        settings, instruments, chains, defaults = chart_setup
        app = create_chart_app(settings, instruments, chains, defaults)
        client = TestClient(app)

        resp = client.get("/api/candles?product=futures:ES&timescale_unit=sec&timescale_nb=1")
        assert resp.status_code == 400

    def test_get_meta_returns_json(self, chart_setup):
        """GET /api/meta retourne tick_size pour futures."""
        settings, instruments, chains, defaults = chart_setup
        app = create_chart_app(settings, instruments, chains, defaults)
        client = TestClient(app)

        resp = client.get("/api/meta?product=futures:ES")
        assert resp.status_code == 200
        data = resp.json()
        assert data["product"] == "futures:ES"
        assert data["tick_size"] == 0.25
        assert data["total_candles"] == 5

    def test_get_meta_unknown_product_404(self, chart_setup):
        settings, instruments, chains, defaults = chart_setup
        app = create_chart_app(settings, instruments, chains, defaults)
        client = TestClient(app)

        resp = client.get("/api/meta?product=UNKNOWN")
        assert resp.status_code == 404

    def test_chart_html_injects_product(self, chart_setup):
        """La page HTML injecte la clé instrument et les defaults."""
        settings, instruments, chains, defaults = chart_setup
        app = create_chart_app(settings, instruments, chains, defaults)
        client = TestClient(app)

        resp = client.get("/futures:ES")
        text = resp.text
        assert '"__PRODUCT__"' not in text
        assert "futures:ES" in text
        assert "50000" in text  # max_visible_candles
