"""Optimisation MPT long-only (equal, min-vol, max-sharpe, frontier).

- equal / min-vol / max-sharpe : candidats analytiques projetés + tirages Dirichlet
  (zero-dep hors numpy).
- frontier : **QP target-return grid** (scipy.optimize.minimize SLSQP) long-only,
  avec fallback Dirichlet si scipy absent ou échec numérique.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from myquantstore.analytics.metrics import cov_array, mean_vector
from myquantstore.analytics.returns import ReturnsFrame


@dataclass(slots=True)
class PortfolioResult:
    """Poids et performance d'un portefeuille."""

    weights: dict[str, float]
    mean_ann: float
    vol_ann: float
    sharpe: float
    objective: str
    symbols: list[str]

    def weights_frame(self, *, min_weight: float = 1e-4) -> pl.DataFrame:
        rows = [
            {"symbol": s, "weight": self.weights[s]}
            for s in self.symbols
            if self.weights[s] >= min_weight
        ]
        if not rows:
            rows = [{"symbol": s, "weight": self.weights[s]} for s in self.symbols]
        return pl.DataFrame(rows).sort("weight", descending=True)


@dataclass(slots=True)
class FrontierPoint:
    mean_ann: float
    vol_ann: float
    sharpe: float
    weights: dict[str, float]


def _project_long_only(w: np.ndarray) -> np.ndarray:
    """Projette sur le simplex {w ≥ 0, sum w = 1}."""
    w = np.maximum(w, 0.0)
    s = w.sum()
    if s <= 1e-15:
        return np.full_like(w, 1.0 / len(w))
    return w / s


def _portfolio_stats(
    w: np.ndarray,
    mu: np.ndarray,
    cov: np.ndarray,
    risk_free_rate: float,
) -> tuple[float, float, float]:
    ret = float(w @ mu)
    var = float(w @ cov @ w)
    vol = float(np.sqrt(max(var, 0.0)))
    sharpe = (ret - risk_free_rate) / vol if vol > 1e-15 else float("nan")
    return ret, vol, sharpe


def portfolio_performance(
    weights: np.ndarray | dict[str, float],
    rf: ReturnsFrame,
    *,
    risk_free_rate: float = 0.04,
    symbols: list[str] | None = None,
) -> tuple[float, float, float]:
    """(mean_ann, vol_ann, sharpe) pour des poids donnés."""
    syms = symbols or rf.symbols
    if isinstance(weights, dict):
        w = np.array([weights.get(s, 0.0) for s in syms], dtype=np.float64)
    else:
        w = np.asarray(weights, dtype=np.float64)
    w = _project_long_only(w)
    mu = mean_vector(rf, annualize=True)
    cov = cov_array(rf, annualize=True)
    return _portfolio_stats(w, mu, cov, risk_free_rate)


def equal_weight(
    rf: ReturnsFrame,
    *,
    risk_free_rate: float = 0.04,
) -> PortfolioResult:
    n = rf.n_assets
    w = np.full(n, 1.0 / n)
    mu = mean_vector(rf, annualize=True)
    cov = cov_array(rf, annualize=True)
    ret, vol, sharpe = _portfolio_stats(w, mu, cov, risk_free_rate)
    return PortfolioResult(
        weights={s: float(wi) for s, wi in zip(rf.symbols, w, strict=True)},
        mean_ann=ret,
        vol_ann=vol,
        sharpe=sharpe,
        objective="equal",
        symbols=list(rf.symbols),
    )


def _sample_simplex(n: int, n_samples: int, rng: np.random.Generator) -> np.ndarray:
    """Tirages uniformes sur le simplex (Dirichlet α=1)."""
    return rng.dirichlet(np.ones(n), size=n_samples)


def _analytical_min_var(cov: np.ndarray) -> np.ndarray:
    """Min variance non contrainte : w ∝ Σ^{-1} 1."""
    ones = np.ones(cov.shape[0])
    try:
        inv = np.linalg.solve(cov, ones)
    except np.linalg.LinAlgError:
        inv = np.linalg.pinv(cov) @ ones
    return _project_long_only(inv)


def _analytical_max_sharpe(mu: np.ndarray, cov: np.ndarray, rf: float) -> np.ndarray:
    """Max Sharpe non contraint : w ∝ Σ^{-1}(μ - rf)."""
    excess = mu - rf
    try:
        inv = np.linalg.solve(cov, excess)
    except np.linalg.LinAlgError:
        inv = np.linalg.pinv(cov) @ excess
    return _project_long_only(inv)


def min_volatility(
    rf: ReturnsFrame,
    *,
    risk_free_rate: float = 0.04,
    n_samples: int = 5000,
    seed: int = 42,
) -> PortfolioResult:
    """Portefeuille long-only à volatilité minimisée."""
    mu = mean_vector(rf, annualize=True)
    cov = cov_array(rf, annualize=True)
    n = rf.n_assets
    rng = np.random.default_rng(seed)

    candidates = [_analytical_min_var(cov), np.full(n, 1.0 / n)]
    samples = _sample_simplex(n, n_samples, rng)
    best_w = candidates[0]
    best_vol = np.inf
    best_ret = 0.0
    best_sh = float("nan")

    for w in list(candidates) + list(samples):
        w = _project_long_only(np.asarray(w, dtype=np.float64))
        ret, vol, sharpe = _portfolio_stats(w, mu, cov, risk_free_rate)
        if vol < best_vol - 1e-15:
            best_vol = vol
            best_w = w
            best_ret = ret
            best_sh = sharpe

    return PortfolioResult(
        weights={s: float(wi) for s, wi in zip(rf.symbols, best_w, strict=True)},
        mean_ann=best_ret,
        vol_ann=best_vol,
        sharpe=best_sh,
        objective="min-vol",
        symbols=list(rf.symbols),
    )


def max_sharpe(
    rf: ReturnsFrame,
    *,
    risk_free_rate: float = 0.04,
    n_samples: int = 5000,
    seed: int = 42,
) -> PortfolioResult:
    """Portefeuille long-only max Sharpe ratio."""
    mu = mean_vector(rf, annualize=True)
    cov = cov_array(rf, annualize=True)
    n = rf.n_assets
    rng = np.random.default_rng(seed)

    candidates = [
        _analytical_max_sharpe(mu, cov, risk_free_rate),
        _analytical_min_var(cov),
        np.full(n, 1.0 / n),
    ]
    samples = _sample_simplex(n, n_samples, rng)
    best_w = candidates[0]
    best_sh = -np.inf
    best_ret = 0.0
    best_vol = 0.0

    for w in list(candidates) + list(samples):
        w = _project_long_only(np.asarray(w, dtype=np.float64))
        ret, vol, sharpe = _portfolio_stats(w, mu, cov, risk_free_rate)
        if np.isnan(sharpe):
            continue
        if sharpe > best_sh:
            best_sh = sharpe
            best_w = w
            best_ret = ret
            best_vol = vol

    return PortfolioResult(
        weights={s: float(wi) for s, wi in zip(rf.symbols, best_w, strict=True)},
        mean_ann=best_ret,
        vol_ann=best_vol,
        sharpe=float(best_sh),
        objective="max-sharpe",
        symbols=list(rf.symbols),
    )


def _min_var_target_return(
    mu: np.ndarray,
    cov: np.ndarray,
    target: float,
    *,
    w0: np.ndarray | None = None,
) -> np.ndarray | None:
    """min w'Σw s.t. w'μ = target, sum w = 1, w ≥ 0 (SLSQP)."""
    try:
        from scipy.optimize import minimize
    except ImportError:
        return None

    n = mu.shape[0]
    x0 = _project_long_only(w0 if w0 is not None else np.full(n, 1.0 / n))

    def objective(w: np.ndarray) -> float:
        return float(w @ cov @ w)

    def grad(w: np.ndarray) -> np.ndarray:
        return 2.0 * (cov @ w)

    constraints = [
        {"type": "eq", "fun": lambda w: float(np.sum(w) - 1.0)},
        {"type": "eq", "fun": lambda w, t=target: float(w @ mu - t)},
    ]
    bounds = [(0.0, 1.0)] * n
    res = minimize(
        objective,
        x0,
        jac=grad,
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
        options={"maxiter": 500, "ftol": 1e-12, "disp": False},
    )
    if not res.success:
        return None
    return _project_long_only(np.asarray(res.x, dtype=np.float64))


def _frontier_dirichlet_fallback(
    mu: np.ndarray,
    cov: np.ndarray,
    risk_free_rate: float,
    *,
    n_samples: int,
    n_points: int,
    seed: int,
) -> pl.DataFrame:
    """Ancien algo : sampling Dirichlet + buckets Pareto (fallback)."""
    n = mu.shape[0]
    rng = np.random.default_rng(seed)
    seeds = [
        _analytical_min_var(cov),
        _analytical_max_sharpe(mu, cov, risk_free_rate),
        np.full(n, 1.0 / n),
    ]
    samples = _sample_simplex(n, n_samples, rng)
    all_w = np.vstack([np.asarray(seeds, dtype=np.float64), samples])

    rets = np.empty(all_w.shape[0])
    vols = np.empty(all_w.shape[0])
    sharpes = np.empty(all_w.shape[0])
    for i, w in enumerate(all_w):
        w = _project_long_only(w)
        all_w[i] = w
        r, v, s = _portfolio_stats(w, mu, cov, risk_free_rate)
        rets[i] = r
        vols[i] = v
        sharpes[i] = s

    r_min, r_max = float(np.nanmin(rets)), float(np.nanmax(rets))
    if r_max - r_min < 1e-12:
        idx = int(np.nanargmin(vols))
        return pl.DataFrame(
            {
                "mean_ann": [rets[idx]],
                "vol_ann": [vols[idx]],
                "sharpe": [sharpes[idx]],
            }
        )

    edges = np.linspace(r_min, r_max, n_points + 1)
    rows: list[dict[str, float]] = []
    for b in range(n_points):
        mask = (rets >= edges[b]) & (rets <= edges[b + 1])
        if not np.any(mask):
            continue
        idx_local = np.where(mask)[0]
        best = idx_local[np.argmin(vols[idx_local])]
        rows.append(
            {
                "mean_ann": float(rets[best]),
                "vol_ann": float(vols[best]),
                "sharpe": float(sharpes[best]),
            }
        )

    if not rows:
        return pl.DataFrame({"mean_ann": [], "vol_ann": [], "sharpe": []})

    out = pl.DataFrame(rows).sort("vol_ann")
    keep: list[int] = []
    best_mu = -np.inf
    for i, row in enumerate(out.iter_rows(named=True)):
        if row["mean_ann"] >= best_mu - 1e-12:
            keep.append(i)
            best_mu = max(best_mu, row["mean_ann"])
    return out[keep]


def efficient_frontier(
    rf: ReturnsFrame,
    *,
    risk_free_rate: float = 0.04,
    n_samples: int = 5000,
    n_points: int = 40,
    seed: int = 42,
    method: str = "qp",
) -> pl.DataFrame:
    """Frontière efficiente long-only.

    :param method: ``qp`` (défaut) = grille de target-return + SLSQP ;
        ``sample`` = Dirichlet + buckets (legacy / fallback).
    Colonnes : mean_ann, vol_ann, sharpe.
    """
    mu = mean_vector(rf, annualize=True)
    cov = cov_array(rf, annualize=True)
    method_l = method.strip().lower()

    if method_l in ("sample", "dirichlet", "montecarlo"):
        return _frontier_dirichlet_fallback(
            mu, cov, risk_free_rate, n_samples=n_samples, n_points=n_points, seed=seed
        )

    # Bornes de return : min-vol analytique projeté → max μ individuel (long-only)
    w_mv = _analytical_min_var(cov)
    r_min, _, _ = _portfolio_stats(w_mv, mu, cov, risk_free_rate)
    r_max = float(np.max(mu))
    # Si tous les μ sont ~égaux, un seul point
    if r_max - r_min < 1e-12:
        ret, vol, sharpe = _portfolio_stats(w_mv, mu, cov, risk_free_rate)
        return pl.DataFrame(
            {"mean_ann": [ret], "vol_ann": [vol], "sharpe": [sharpe]}
        )

    targets = np.linspace(r_min, r_max, max(n_points, 2))
    rows: list[dict[str, float]] = []
    w_prev = w_mv
    qp_ok = 0
    for t in targets:
        w = _min_var_target_return(mu, cov, float(t), w0=w_prev)
        if w is None:
            continue
        ret, vol, sharpe = _portfolio_stats(w, mu, cov, risk_free_rate)
        rows.append({"mean_ann": ret, "vol_ann": vol, "sharpe": sharpe})
        w_prev = w
        qp_ok += 1

    # Si trop peu de points QP, compléter / remplacer par sampling
    if qp_ok < max(3, n_points // 4):
        return _frontier_dirichlet_fallback(
            mu, cov, risk_free_rate, n_samples=n_samples, n_points=n_points, seed=seed
        )

    out = pl.DataFrame(rows).sort("vol_ann")
    # filtre Pareto : vol croissante ⇒ mean non décroissante
    keep: list[int] = []
    best_mu = -np.inf
    for i, row in enumerate(out.iter_rows(named=True)):
        if row["mean_ann"] >= best_mu - 1e-12:
            keep.append(i)
            best_mu = max(best_mu, row["mean_ann"])
    return out[keep]



def optimize(
    rf: ReturnsFrame,
    objective: str,
    *,
    risk_free_rate: float = 0.04,
    n_samples: int = 5000,
    seed: int = 42,
) -> PortfolioResult:
    """Dispatch equal | min-vol | max-sharpe."""
    obj = objective.strip().lower().replace("_", "-")
    if obj in ("equal", "ew", "1/n"):
        return equal_weight(rf, risk_free_rate=risk_free_rate)
    if obj in ("min-vol", "minvol", "min-variance", "min-var"):
        return min_volatility(
            rf, risk_free_rate=risk_free_rate, n_samples=n_samples, seed=seed
        )
    if obj in ("max-sharpe", "sharpe", "tangency"):
        return max_sharpe(
            rf, risk_free_rate=risk_free_rate, n_samples=n_samples, seed=seed
        )
    raise ValueError(
        f"objective inconnu: {objective!r}. "
        "Utilisez: equal | min-vol | max-sharpe"
    )
