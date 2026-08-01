"""Statistiques individuelles, corrélation et covariance annualisées."""

from __future__ import annotations

import numpy as np
import polars as pl

from myquantstore.analytics.returns import ReturnsFrame


def asset_stats(
    rf: ReturnsFrame,
    *,
    risk_free_rate: float = 0.04,
) -> pl.DataFrame:
    """Stats annualisées par titre : mean, vol, sharpe, n_obs."""
    mat = rf.matrix  # (T, N)
    ppy = rf.periods_per_year
    mu = mat.mean(axis=0) * ppy
    # ddof=1 sample
    sig = mat.std(axis=0, ddof=1) * np.sqrt(ppy)
    excess = mu - risk_free_rate
    sharpe = np.where(sig > 1e-15, excess / sig, np.nan)

    return pl.DataFrame(
        {
            "symbol": rf.symbols,
            "mean_ann": mu,
            "vol_ann": sig,
            "sharpe": sharpe,
            "n_obs": [rf.n_obs] * len(rf.symbols),
        }
    ).sort("sharpe", descending=True, nulls_last=True)


def correlation_matrix(rf: ReturnsFrame) -> pl.DataFrame:
    """Matrice de corrélation des returns (colonnes = symboles + index symbol)."""
    mat = rf.matrix
    # numpy corrcoef expects variables in rows
    corr = np.corrcoef(mat, rowvar=False)
    data: dict[str, object] = {"symbol": rf.symbols}
    for j, sym in enumerate(rf.symbols):
        data[sym] = corr[:, j]
    return pl.DataFrame(data)


def covariance_matrix(rf: ReturnsFrame, *, annualize: bool = True) -> pl.DataFrame:
    """Matrice de covariance (annualisée par défaut)."""
    mat = rf.matrix
    cov = np.cov(mat, rowvar=False, ddof=1)
    if annualize:
        cov = cov * rf.periods_per_year
    data: dict[str, object] = {"symbol": rf.symbols}
    for j, sym in enumerate(rf.symbols):
        data[sym] = cov[:, j]
    return pl.DataFrame(data)


def mean_vector(rf: ReturnsFrame, *, annualize: bool = True) -> np.ndarray:
    """Vecteur μ (N,) annualisé si demandé."""
    mu = rf.matrix.mean(axis=0)
    if annualize:
        mu = mu * rf.periods_per_year
    return mu.astype(np.float64)


def cov_array(rf: ReturnsFrame, *, annualize: bool = True) -> np.ndarray:
    """Σ (N, N) numpy."""
    cov = np.cov(rf.matrix, rowvar=False, ddof=1).astype(np.float64)
    if annualize:
        cov = cov * rf.periods_per_year
    # régularisation légère pour stabilité numérique
    n = cov.shape[0]
    cov = cov + np.eye(n) * 1e-10
    return cov
