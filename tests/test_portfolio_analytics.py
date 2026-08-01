"""Tests analytics portfolio (MPT) — panel synthétique, pas d'I/O réseau."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from myquantstore.analytics.metrics import (
    asset_stats,
    correlation_matrix,
    cov_array,
    mean_vector,
)
from myquantstore.analytics.optimize import (
    equal_weight,
    efficient_frontier,
    max_sharpe,
    min_volatility,
    optimize,
)
from myquantstore.analytics.panel import PricePanel
from myquantstore.analytics.returns import ReturnsFrame, compute_returns


def _synthetic_panel(n_days: int = 252 * 3, seed: int = 0) -> PricePanel:
    """3 actifs : A bas risque, B haut risque/return, C corrélé à B."""
    rng = np.random.default_rng(seed)
    dates = [date(2020, 1, 2) + timedelta(days=i) for i in range(n_days)]
    # GBM-like
    r_a = rng.normal(0.0002, 0.008, n_days)
    r_b = rng.normal(0.0006, 0.018, n_days)
    r_c = 0.7 * r_b + 0.3 * rng.normal(0.0004, 0.012, n_days)
    p_a = 100 * np.cumprod(1 + r_a)
    p_b = 50 * np.cumprod(1 + r_b)
    p_c = 80 * np.cumprod(1 + r_c)
    prices = pl.DataFrame(
        {
            "date": dates,
            "AAA": p_a,
            "BBB": p_b,
            "CCC": p_c,
        }
    )
    return PricePanel(
        prices=prices,
        symbols=["AAA", "BBB", "CCC"],
        timescale="day",
    )


@pytest.fixture
def rets(tmp_settings) -> ReturnsFrame:
    panel = _synthetic_panel()
    return compute_returns(panel, tmp_settings, kind="simple")


class TestReturns:
    def test_simple_returns_shape(self, rets):
        assert rets.n_assets == 3
        assert rets.n_obs >= 100
        assert rets.matrix.shape == (rets.n_obs, 3)

    def test_log_returns(self, tmp_settings):
        panel = _synthetic_panel(n_days=100)
        rf = compute_returns(panel, tmp_settings, kind="log")
        assert rf.kind == "log"
        assert np.isfinite(rf.matrix).all()


class TestMetrics:
    def test_asset_stats_columns(self, rets):
        stats = asset_stats(rets, risk_free_rate=0.02)
        assert set(stats.columns) >= {"symbol", "mean_ann", "vol_ann", "sharpe"}
        assert stats.height == 3
        # AAA devrait avoir vol plus faible que BBB
        vols = {r["symbol"]: r["vol_ann"] for r in stats.iter_rows(named=True)}
        assert vols["AAA"] < vols["BBB"]

    def test_corr_diagonal_one(self, rets):
        corr = correlation_matrix(rets)
        for row in corr.iter_rows(named=True):
            assert abs(float(row[row["symbol"]]) - 1.0) < 1e-9


class TestOptimize:
    def test_equal_weight_sums_to_one(self, rets):
        res = equal_weight(rets, risk_free_rate=0.02)
        assert abs(sum(res.weights.values()) - 1.0) < 1e-9
        assert res.objective == "equal"
        assert res.vol_ann > 0

    def test_min_vol_lower_or_eq_equal(self, rets):
        eq = equal_weight(rets, risk_free_rate=0.02)
        mv = min_volatility(rets, risk_free_rate=0.02, n_samples=2000, seed=1)
        assert mv.vol_ann <= eq.vol_ann + 1e-6
        assert abs(sum(mv.weights.values()) - 1.0) < 1e-9
        assert all(w >= -1e-12 for w in mv.weights.values())

    def test_max_sharpe_beats_equal_sharpe(self, rets):
        eq = equal_weight(rets, risk_free_rate=0.02)
        ms = max_sharpe(rets, risk_free_rate=0.02, n_samples=3000, seed=2)
        # Sur données synthétiques biaisées, max-sharpe >= equal (tolérance grille)
        assert ms.sharpe + 0.05 >= eq.sharpe
        assert abs(sum(ms.weights.values()) - 1.0) < 1e-9

    def test_optimize_dispatch(self, rets):
        r = optimize(rets, "min-vol", risk_free_rate=0.02, n_samples=500, seed=0)
        assert r.objective == "min-vol"

    def test_optimize_unknown(self, rets):
        with pytest.raises(ValueError, match="objective"):
            optimize(rets, "foo")

    def test_frontier_non_empty(self, rets):
        fr = efficient_frontier(rets, risk_free_rate=0.02, n_samples=1500, n_points=20, seed=3)
        assert fr.height >= 1
        assert {"mean_ann", "vol_ann", "sharpe"} <= set(fr.columns)
        # vol croissante approximative après filtre
        vols = fr["vol_ann"].to_list()
        assert vols == sorted(vols)


class TestNumpyHelpers:
    def test_mean_cov_shapes(self, rets):
        mu = mean_vector(rets)
        cov = cov_array(rets)
        assert mu.shape == (3,)
        assert cov.shape == (3, 3)
        # SPD-ish
        eig = np.linalg.eigvalsh(cov)
        assert np.all(eig > 0)
