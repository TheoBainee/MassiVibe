"""Tests du module query/adjust.py (ajustement split)."""

from __future__ import annotations

from datetime import datetime

import polars as pl
import pytest

from myquantstore.query.adjust import apply_dividend_adjustment, apply_split_adjustment


def _price_df(prices: list[float], dates: list[str]) -> pl.DataFrame:
    """Crée un DataFrame de chandeliers bruts (prix non ajustés)."""
    ts = [datetime.fromisoformat(d + "T09:30:00+00:00") for d in dates]
    return pl.DataFrame(
        {
            "window_start": ts,
            "open": prices,
            "high": [p + 1 for p in prices],
            "low": [p - 1 for p in prices],
            "close": [p + 0.5 for p in prices],
        }
    )


def _splits_df() -> pl.DataFrame:
    """Splits AAPL : un forward_split 4-pour-1 le 2020-08-28 (factor=0.25)."""
    return pl.DataFrame(
        {
            "execution_date": [__import__("datetime").date(2020, 8, 28)],
            "historical_adjustment_factor": [0.25],
            "adjustment_type": ["forward_split"],
            "split_from": [1],
            "split_to": [4],
            "ticker": ["AAPL"],
        }
    )


class TestApplySplitAdjustment:
    def test_pre_split_prices_adjusted(self):
        """Les prix avant le split sont multipliés par 0.25 (factor)."""
        df = _price_df([400.0, 404.0], ["2020-08-26", "2020-08-27"])  # avant split 2020-08-28
        splits = _splits_df()

        result = apply_split_adjustment(df, splits)

        # Avant le split, prix brut = 400 -> ajusté = 400 * 0.25 = 100
        assert result["open"][0] == 100.0
        assert result["open"][1] == 101.0

    def test_post_split_prices_unchanged(self):
        """Les prix après le split ne sont pas ajustés (factor=1.0)."""
        df = _price_df([100.0, 101.0], ["2020-08-28", "2020-08-31"])  # après split
        splits = _splits_df()

        result = apply_split_adjustment(df, splits)

        assert result["open"][0] == 100.0
        assert result["open"][1] == 101.0

    def test_mixed_pre_post(self):
        """Mixte avant/après split : seuls les prix antérieurs sont ajustés."""
        df = _price_df([400.0, 100.0], ["2020-08-27", "2020-08-28"])
        splits = _splits_df()

        result = apply_split_adjustment(df, splits)

        assert result["open"][0] == 100.0  # 400 * 0.25
        assert result["open"][1] == 100.0  # inchangé

    def test_no_splits_noop(self):
        """Aucun split → pas d'ajustement."""
        df = _price_df([100.0], ["2024-01-02"])
        result = apply_split_adjustment(df, pl.DataFrame())
        assert result["open"].to_list() == [100.0]

    def test_all_price_columns_adjusted(self):
        """Toutes les colonnes OHLC sont ajustées."""
        df = _price_df([400.0], ["2020-08-27"])
        splits = _splits_df()

        result = apply_split_adjustment(df, splits)

        assert result["open"][0] == 100.0
        assert result["high"][0] == (400.0 + 1) * 0.25
        assert result["low"][0] == (400.0 - 1) * 0.25
        assert result["close"][0] == (400.0 + 0.5) * 0.25


class TestApplyDividendAdjustment:
    def test_pre_dividend_prices_adjusted(self):
        """Les prix avant le dividend sont multipliés par le facteur."""
        # Facteur exemple ~0.9979 comme dans doc API
        dividends = pl.DataFrame(
            {
                "ex_dividend_date": [__import__("datetime").date(2024, 1, 10)],
                "historical_adjustment_factor": [0.9979],
            }
        )
        df = _price_df([100.0, 101.0], ["2024-01-08", "2024-01-09"])  # avant ex-date

        result = apply_dividend_adjustment(df, dividends)

        assert abs(result["open"][0] - 100.0 * 0.9979) < 0.0001
        assert abs(result["open"][1] - 101.0 * 0.9979) < 0.0001

    def test_post_dividend_prices_unchanged(self):
        """Les prix après le dividend ne sont pas ajustés."""
        dividends = pl.DataFrame(
            {
                "ex_dividend_date": [__import__("datetime").date(2024, 1, 10)],
                "historical_adjustment_factor": [0.9979],
            }
        )
        df = _price_df([100.0], ["2024-01-11"])  # après

        result = apply_dividend_adjustment(df, dividends)

        assert result["open"][0] == 100.0

    def test_no_dividends_noop(self):
        """Aucun dividend → pas d'ajustement."""
        df = _price_df([100.0], ["2024-01-02"])
        result = apply_dividend_adjustment(df, pl.DataFrame())
        assert result["open"].to_list() == [100.0]
