"""Tests helpers résolution / familles de timeframes."""

import pytest

from myquantstore.instruments import (
    DEFAULT_RESOLUTION,
    RESOLUTION_1DAY,
    RESOLUTION_1MIN,
    TF_FAMILY_EXTRADAY,
    TF_FAMILY_INTRADAY,
    base_resolution_for_timeframe,
    parse_timeframe,
    timeframe_family,
)


def test_defaults():
    assert DEFAULT_RESOLUTION == RESOLUTION_1MIN == "1min"
    assert RESOLUTION_1DAY == "1day"


@pytest.mark.parametrize(
    ("tf", "family", "base"),
    [
        ("1min", TF_FAMILY_INTRADAY, "1min"),
        ("5min", TF_FAMILY_INTRADAY, "1min"),
        ("1hour", TF_FAMILY_INTRADAY, "1min"),
        ("4hour", TF_FAMILY_INTRADAY, "1min"),
        ("1day", TF_FAMILY_EXTRADAY, "1day"),
        ("2day", TF_FAMILY_EXTRADAY, "1day"),
        ("1week", TF_FAMILY_EXTRADAY, "1day"),
    ],
)
def test_timeframe_family_and_base(tf: str, family: str, base: str):
    assert timeframe_family(tf) == family
    assert base_resolution_for_timeframe(tf) == base


def test_parse_timeframe_day_week():
    assert parse_timeframe("1day") == (1, "day")
    assert parse_timeframe("2day") == (2, "day")
    assert parse_timeframe("1week") == (1, "week")
    assert parse_timeframe("1min") == (1, "minute")
