"""Tests RF dynamique Yahoo (^IRX) + resolve."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from myquantstore.analytics.risk_free import (
    RiskFreeQuote,
    fetch_yahoo_risk_free,
    resolve_risk_free_rate,
    _yield_pct_to_rate,
)
from myquantstore.api.yahoo import YahooError
from myquantstore.storage.parquet_io import write_parquet


def test_yield_pct_to_rate():
    assert _yield_pct_to_rate(3.70) == pytest.approx(0.037)
    assert _yield_pct_to_rate(0.0) == 0.0


def test_resolve_cli_override(tmp_settings):
    tmp_settings.portfolio_rf_source = "yahoo"
    q = resolve_risk_free_rate(tmp_settings, cli_rf=0.055)
    assert q.source == "cli"
    assert q.rate == pytest.approx(0.055)


def test_resolve_static(tmp_settings):
    tmp_settings.portfolio_rf_source = "static"
    tmp_settings.portfolio_risk_free_rate = 0.041
    q = resolve_risk_free_rate(tmp_settings)
    assert q.source == "static"
    assert q.rate == pytest.approx(0.041)


def test_fetch_yahoo_from_cache(tmp_settings):
    """Sans réseau : peupler le cache parquet puis lire."""
    from myquantstore.analytics.risk_free import _cache_path

    path = _cache_path(tmp_settings, "^IRX")
    df = pl.DataFrame(
        {
            "session_end_date": [date(2026, 7, 1), date(2026, 7, 2)],
            "close": [3.5, 3.8],
        }
    )
    write_parquet(
        df,
        path,
        kind="risk_free",
        yahoo_ticker="^IRX",
        last_fetched_at="2099-01-01T00:00:00+00:00",  # frais longtemps
        unit="percent_yield",
    )
    q = fetch_yahoo_risk_free(tmp_settings, yahoo_ticker="^IRX", force_refresh=False)
    assert q.source == "yahoo"
    assert q.rate == pytest.approx(0.038)
    assert q.as_of == date(2026, 7, 2)


def test_resolve_yahoo_fallback_on_error(tmp_settings, monkeypatch):
    tmp_settings.portfolio_rf_source = "yahoo"
    tmp_settings.portfolio_risk_free_rate = 0.044

    def boom(*_a, **_k):
        raise YahooError("network down")

    monkeypatch.setattr(
        "myquantstore.analytics.risk_free.fetch_yahoo_risk_free", boom
    )
    q = resolve_risk_free_rate(tmp_settings)
    assert q.source == "static"
    assert q.rate == pytest.approx(0.044)
    assert "fallback" in q.detail
