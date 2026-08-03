"""Panel portfolio (fixtures parquet 1day) + monotonicité frontier QP."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from myquantstore.analytics.optimize import efficient_frontier
from myquantstore.analytics.panel import build_price_panel
from myquantstore.analytics.returns import compute_returns
from myquantstore.instruments import Instrument, InstrumentType
from myquantstore.pipeline.aggregator import aggregate
from myquantstore.storage.raw_dumps import save_raw_dump


def _seed_yahoo_actions_empty(symbol: str, settings) -> None:
    """Cache splits/dividends Yahoo vide + frais → pas d'appel réseau en query."""
    from datetime import UTC, datetime

    import polars as pl

    from myquantstore.storage.parquet_io import write_parquet

    now = datetime.now(UTC).isoformat()
    for kind in ("splits", "dividends"):
        path = settings.yahoo_actions_path(symbol, kind)
        if kind == "splits":
            df = pl.DataFrame(
                schema={
                    "execution_date": pl.Date,
                    "historical_adjustment_factor": pl.Float64,
                    "split_ratio": pl.Float64,
                }
            )
        else:
            df = pl.DataFrame(
                schema={
                    "ex_dividend_date": pl.Date,
                    "historical_adjustment_factor": pl.Float64,
                    "amount": pl.Float64,
                }
            )
        write_parquet(
            df,
            path,
            kind=f"yahoo_actions.{kind}",
            symbol=symbol,
            last_fetched_at=now,
        )



def _seed_stock_1day(
    symbol: str,
    settings,
    *,
    n: int = 80,
    base: float = 100.0,
    drift: float = 0.001,
    start: datetime | None = None,
    skip_last: int = 0,
) -> Instrument:
    """Écrit dump + agrégat 1day Yahoo pour un stock (prix croissants simples)."""
    inst = Instrument(type=InstrumentType.STOCKS, symbol=symbol)
    t0 = start or datetime(2024, 1, 2)
    n_eff = n - skip_last
    ts = [t0 + timedelta(days=i) for i in range(n_eff)]
    closes = [base * ((1.0 + drift) ** i) for i in range(n_eff)]
    df = pl.DataFrame(
        {
            "window_start": ts,
            "ticker": [symbol] * n_eff,
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [1000] * n_eff,
            "symbol": [symbol] * n_eff,
            "instrument_type": ["stocks"] * n_eff,
            "product_code": [symbol] * n_eff,
            "run_id": ["t"] * n_eff,
            "session_end_date": [t.date() for t in ts],
            "dollar_volume": [c * 1000 for c in closes],
            "transactions": [10] * n_eff,
        }
    )
    save_raw_dump(
        df, inst, symbol, "20240102T120000", settings, resolution="1day", source="yahoo"
    )
    aggregate(inst, settings, resolution="1day")
    _seed_yahoo_actions_empty(symbol, settings)
    return inst


class TestPanelParquetFixtures:
    def test_build_panel_three_stocks(self, tmp_settings):
        tmp_settings.stocks = ["AAPL", "MSFT", "GOOG"]
        _seed_stock_1day("AAPL", tmp_settings, base=100.0, drift=0.001)
        _seed_stock_1day("MSFT", tmp_settings, base=200.0, drift=0.0012)
        _seed_stock_1day("GOOG", tmp_settings, base=150.0, drift=0.0008)

        instruments = [
            Instrument(InstrumentType.STOCKS, s) for s in ("AAPL", "MSFT", "GOOG")
        ]
        panel = build_price_panel(
            instruments,
            tmp_settings,
            start="2024-01-01",
            end="2024-06-01",
            timescale="day",
            adjust_dividends=False,
            min_coverage=0.5,
        )
        assert panel.n_assets == 3
        assert panel.n_obs >= 20
        assert set(panel.symbols) == {"AAPL", "MSFT", "GOOG"}
        # inner join → pas de null
        for s in panel.symbols:
            assert panel.prices[s].null_count() == 0

    def test_coverage_drop_short_history(self, tmp_settings):
        """Un titre avec très peu de barres est exclu sous min_coverage."""
        tmp_settings.stocks = ["FULL", "SHORT", "OK2"]
        _seed_stock_1day("FULL", tmp_settings, n=100, base=100.0, drift=0.001)
        _seed_stock_1day("OK2", tmp_settings, n=100, base=80.0, drift=0.0011)
        # SHORT : 8 jours seulement → coverage ~8% sur l'union des dates
        _seed_stock_1day("SHORT", tmp_settings, n=8, base=50.0, drift=0.001)

        instruments = [
            Instrument(InstrumentType.STOCKS, s) for s in ("FULL", "SHORT", "OK2")
        ]
        panel = build_price_panel(
            instruments,
            tmp_settings,
            start="2024-01-01",
            end="2024-06-01",
            timescale="day",
            adjust_dividends=False,
            min_coverage=0.5,
        )
        assert "SHORT" in panel.dropped
        assert "SHORT" not in panel.symbols
        assert set(panel.symbols) >= {"FULL", "OK2"}
        assert any("coverage" in w for w in panel.warnings)


class TestFrontierMonotonicity:
    def test_qp_frontier_vol_and_return_monotone(self, tmp_settings):
        """Sur frontière triée par vol : mean_ann non décroissant (Pareto)."""
        from myquantstore.analytics.panel import PricePanel

        rng = np.random.default_rng(7)
        n_days = 252 * 4
        dates = [date(2019, 1, 2) + timedelta(days=i) for i in range(n_days)]
        # 4 actifs avec profils distincts
        cols = {"date": dates}
        for name, mu, sig in (
            ("LOW", 0.00015, 0.006),
            ("MID", 0.0004, 0.012),
            ("HIGH", 0.0007, 0.02),
            ("MIX", 0.0005, 0.015),
        ):
            r = rng.normal(mu, sig, n_days)
            cols[name] = 100 * np.cumprod(1 + r)
        panel = PricePanel(
            prices=pl.DataFrame(cols),
            symbols=["LOW", "MID", "HIGH", "MIX"],
            timescale="day",
        )
        rets = compute_returns(panel, tmp_settings, kind="simple")
        fr = efficient_frontier(
            rets,
            risk_free_rate=0.02,
            n_points=25,
            n_samples=2000,
            seed=1,
            method="qp",
        )
        assert fr.height >= 3
        vols = fr["vol_ann"].to_list()
        means = fr["mean_ann"].to_list()
        assert vols == sorted(vols)
        for i in range(1, len(means)):
            assert means[i] + 1e-9 >= means[i - 1]

    def test_sample_method_still_works(self, tmp_settings):
        from myquantstore.analytics.panel import PricePanel

        rng = np.random.default_rng(0)
        n = 300
        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]
        prices = pl.DataFrame(
            {
                "date": dates,
                "A": 100 * np.cumprod(1 + rng.normal(0.0003, 0.01, n)),
                "B": 50 * np.cumprod(1 + rng.normal(0.0005, 0.015, n)),
            }
        )
        panel = PricePanel(prices=prices, symbols=["A", "B"], timescale="day")
        rets = compute_returns(panel, tmp_settings, kind="simple")
        fr = efficient_frontier(
            rets, risk_free_rate=0.02, n_points=15, n_samples=800, method="sample", seed=0
        )
        assert fr.height >= 1
        assert {"mean_ann", "vol_ann", "sharpe"} <= set(fr.columns)
