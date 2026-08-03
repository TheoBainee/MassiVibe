"""Allocation discrète : poids théoriques + capital → lots entiers."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import polars as pl

from myquantstore.analytics.optimize import PortfolioResult
from myquantstore.analytics.panel import PricePanel


@dataclass(slots=True)
class DiscreteAllocation:
    """Résultat d'une allocation en parts entières.

    **Poids effectifs** (``weights_eff``) : notionals / **invested** (hors cash).
    Donc ``sum(weights_eff) == 1`` sur les titres achetés, mais
    ``sum(weights_eff * invested) + cash == value`` — les poids ne somment
    **pas** à 1 par rapport au capital total ``value`` tant que ``cash > 0``.
    Comparer ``weights_th`` (cible sur 100 % du capital) à ``weights_eff``
    (réalisé sur la poche investie uniquement).
    """

    shares: dict[str, int]
    prices: dict[str, float]
    weights_th: dict[str, float]
    weights_eff: dict[str, float]
    value: float
    invested: float
    cash: float
    objective: str
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def drift_l1(self) -> float:
        """Distance L1 entre w_th et w_eff (sur union des symboles)."""
        keys = set(self.weights_th) | set(self.weights_eff)
        return float(
            sum(abs(self.weights_th.get(k, 0.0) - self.weights_eff.get(k, 0.0)) for k in keys)
        )

    def lots_frame(self) -> pl.DataFrame:
        rows = []
        for sym, n in sorted(self.shares.items(), key=lambda x: -x[1] * self.prices.get(x[0], 0)):
            if n <= 0:
                continue
            px = self.prices[sym]
            notional = n * px
            rows.append(
                {
                    "symbol": sym,
                    "shares": n,
                    "price": px,
                    "notional": notional,
                    "weight_th": self.weights_th.get(sym, 0.0),
                    "weight_eff": self.weights_eff.get(sym, 0.0),
                }
            )
        if not rows:
            return pl.DataFrame(
                schema={
                    "symbol": pl.Utf8,
                    "shares": pl.Int64,
                    "price": pl.Float64,
                    "notional": pl.Float64,
                    "weight_th": pl.Float64,
                    "weight_eff": pl.Float64,
                }
            )
        return pl.DataFrame(rows)


def latest_prices_from_panel(panel: PricePanel) -> dict[str, float]:
    """Dernier close non-null par symbole du panel."""
    out: dict[str, float] = {}
    last = panel.prices.tail(1)
    for sym in panel.symbols:
        v = last[sym][0]
        if v is not None and float(v) > 0:
            out[sym] = float(v)
    return out


def allocate_discrete(
    result: PortfolioResult,
    prices: dict[str, float],
    value: float,
    *,
    min_weight: float = 1e-6,
) -> DiscreteAllocation:
    """Convertit des poids longs-only en parts entières.

    Algorithme (residual deficit) :
    1. ``target_notional_i = w_i * V`` ; ``shares_i = floor(target / P_i)``
    2. Tant que cash ≥ min prix des titres encore en **déficit**
       (``shares*P < w*V``), acheter 1 share du titre au plus grand déficit
       dollar abordable.
    3. Plafond dur : ``shares_i ≤ ceil(w_i * V / P_i)`` — empêche un titre
       cheap (ex. CPRT à w≈0.2%) d'absorber tout le cash résiduel.

    Titres avec ``P_i > value`` → skip + warning.
    """
    if value <= 0:
        raise ValueError("value doit être > 0")

    weights_th = {
        s: float(w)
        for s, w in result.weights.items()
        if float(w) >= min_weight and s in prices and prices[s] > 0
    }
    skipped: list[str] = []
    warnings: list[str] = []

    for s, w in result.weights.items():
        if float(w) < min_weight:
            continue
        if s not in prices or prices[s] <= 0:
            skipped.append(s)
            warnings.append(f"{s}: prix indisponible — ignoré")
        elif prices[s] > value:
            skipped.append(s)
            warnings.append(
                f"{s}: prix {prices[s]:.2f} > capital {value:.2f} — impossible d'acheter 1 share"
            )
            weights_th.pop(s, None)

    if not weights_th:
        raise ValueError(
            "Aucun titre allocatable (prix manquants ou capital insuffisant). "
            f"skipped={skipped}"
        )

    # Renormalise w_th sur les titres restants
    s_w = sum(weights_th.values())
    weights_th = {k: v / s_w for k, v in weights_th.items()}

    symbols = list(weights_th.keys())
    px = np.array([prices[s] for s in symbols], dtype=np.float64)
    w = np.array([weights_th[s] for s in symbols], dtype=np.float64)
    target_notional = w * value

    # Floor + plafond ceil (évite overshoot massif sur titres cheap)
    shares = np.floor(target_notional / px + 1e-12).astype(np.int64)
    max_shares = np.ceil(target_notional / px - 1e-12).astype(np.int64)
    # Au moins autoriser 0 ; si target < 1 share, max peut être 1 si w*V >= P sinon 0
    max_shares = np.maximum(max_shares, 0)
    for i in range(len(symbols)):
        if target_notional[i] + 1e-9 < px[i]:
            # budget titre < 1 share → ne pas forcer d'achat (max=0)
            max_shares[i] = 0
            shares[i] = 0

    cash = value - float(np.sum(shares * px))

    # Residual : combler les plus gros déficits dollar sans dépasser max_shares
    max_iter = int(np.sum(max_shares - shares)) + len(symbols) + 10
    for _ in range(max(max_iter, 1)):
        deficit = target_notional - shares * px
        # candidats : encore sous cible, pas au plafond, abordables
        best_i = -1
        best_def = 0.0
        for i in range(len(symbols)):
            if shares[i] >= max_shares[i]:
                continue
            if px[i] > cash + 1e-12:
                continue
            if deficit[i] <= 1e-9:
                continue
            if deficit[i] > best_def:
                best_def = float(deficit[i])
                best_i = i
        if best_i < 0:
            break
        shares[best_i] += 1
        cash -= float(px[best_i])

    invested = float(np.sum(shares * px))
    cash = value - invested
    shares_map = {s: int(n) for s, n in zip(symbols, shares, strict=True) if int(n) > 0}
    if invested > 1e-12:
        weights_eff = {
            s: (shares_map.get(s, 0) * prices[s]) / invested
            for s in symbols
            if shares_map.get(s, 0) > 0
        }
    else:
        weights_eff = {}

    return DiscreteAllocation(
        shares=shares_map,
        prices={s: prices[s] for s in shares_map},
        weights_th=weights_th,
        weights_eff=weights_eff,
        value=value,
        invested=invested,
        cash=cash,
        objective=result.objective,
        skipped=skipped,
        warnings=warnings,
    )
