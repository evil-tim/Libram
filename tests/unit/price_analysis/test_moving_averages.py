"""Tests for simple and exponential moving averages."""

from datetime import UTC, datetime, timedelta

import pytest

from price_analysis.moving_averages import compute_ema, compute_sma


def series(values: list[float]) -> list[tuple[datetime, float]]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        (start + timedelta(days=index), value) for index, value in enumerate(values)
    ]


def test_sma_returns_rolling_values_with_dates() -> None:
    assert compute_sma(series([1, 2, 3, 4, 5]), 3) == [
        {"date": "2026-01-03", "value": 2.0},
        {"date": "2026-01-04", "value": 3.0},
        {"date": "2026-01-05", "value": 4.0},
    ]


def test_sma_returns_empty_when_period_exceeds_series() -> None:
    assert compute_sma(series([1, 2]), 3) == []


def test_sma_period_one_returns_each_value() -> None:
    assert compute_sma(series([1.25, 2.5]), 1) == [
        {"date": "2026-01-01", "value": 1.25},
        {"date": "2026-01-02", "value": 2.5},
    ]


def test_ema_seeds_with_sma_then_applies_smoothing() -> None:
    assert compute_ema(series([10, 20, 30, 40]), 3) == [
        {"date": "2026-01-03", "value": 20.0},
        {"date": "2026-01-04", "value": 30.0},
    ]


def test_ema_returns_empty_when_period_exceeds_series() -> None:
    assert compute_ema(series([1, 2]), 3) == []


@pytest.mark.parametrize("period", [0, -1])
def test_moving_averages_reject_non_positive_periods(period: int) -> None:
    with pytest.raises((ZeroDivisionError, IndexError)):
        compute_sma(series([1, 2]), period)
    with pytest.raises((ZeroDivisionError, IndexError)):
        compute_ema(series([1, 2]), period)
