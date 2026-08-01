"""Returns simple/log et facteurs d'annualisation."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from myquantstore.analytics.panel import PricePanel
from myquantstore.config import Settings


@dataclass(slots=True)
class ReturnsFrame:
    """Matrice de returns (lignes = dates, colonnes = symboles)."""

    returns: pl.DataFrame  # date + symboles
    symbols: list[str]
    kind: str  # "simple" | "log"
    periods_per_year: float
    timescale: str

    @property
    def matrix(self) -> np.ndarray:
        """Array (T, N) float64 sans la colonne date."""
        return self.returns.select(self.symbols).to_numpy().astype(np.float64)

    @property
    def n_obs(self) -> int:
        return self.returns.height

    @property
    def n_assets(self) -> int:
        return len(self.symbols)


def periods_per_year(timescale: str, settings: Settings) -> float:
    """Nombre de périodes par an pour annualiser."""
    if timescale == "week":
        return 52.0
    return float(settings.portfolio_trading_days_per_year)


def compute_returns(
    panel: PricePanel,
    settings: Settings,
    *,
    kind: str = "simple",
) -> ReturnsFrame:
    """Calcule les returns à partir d'un panel de prix.

    :param kind: ``simple`` (P_t/P_{t-1}-1) ou ``log``.
    """
    if kind not in ("simple", "log"):
        raise ValueError("kind doit être 'simple' ou 'log'")

    exprs: list[pl.Expr] = [pl.col("date")]
    for sym in panel.symbols:
        prev = pl.col(sym).shift(1)
        if kind == "log":
            exprs.append((pl.col(sym) / prev).log().alias(sym))
        else:
            exprs.append((pl.col(sym) / prev - 1.0).alias(sym))

    rets = panel.prices.select(exprs).slice(1).drop_nulls()
    if rets.height < 2:
        raise ValueError("Pas assez de returns après différenciation.")

    ppy = periods_per_year(panel.timescale, settings)
    return ReturnsFrame(
        returns=rets,
        symbols=list(panel.symbols),
        kind=kind,
        periods_per_year=ppy,
        timescale=panel.timescale,
    )
