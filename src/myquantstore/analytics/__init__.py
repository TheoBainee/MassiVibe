"""Analyse de portefeuille (MPT) sur historiques multi-instruments.

Pipeline typique ::

    panel → returns → metrics (stats/corr/cov) → optimize / frontier

Surface CLI : ``myquantstore portfolio …``. Track extraday ``1day`` (Yahoo)
recommandé pour stocks (total return = split + dividend adjust à la query).
"""

from myquantstore.analytics.allocate import DiscreteAllocation, allocate_discrete
from myquantstore.analytics.metrics import (
    asset_stats,
    correlation_matrix,
    covariance_matrix,
)
from myquantstore.analytics.optimize import (
    efficient_frontier,
    equal_weight,
    max_sharpe,
    min_volatility,
    portfolio_performance,
)
from myquantstore.analytics.panel import PricePanel, build_price_panel
from myquantstore.analytics.returns import ReturnsFrame, compute_returns
from myquantstore.analytics.synthetic import build_portfolio_ohlcv

__all__ = [
    "DiscreteAllocation",
    "PricePanel",
    "ReturnsFrame",
    "allocate_discrete",
    "asset_stats",
    "build_portfolio_ohlcv",
    "build_price_panel",
    "compute_returns",
    "correlation_matrix",
    "covariance_matrix",
    "efficient_frontier",
    "equal_weight",
    "max_sharpe",
    "min_volatility",
    "portfolio_performance",
]
