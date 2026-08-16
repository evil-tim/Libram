"""Tests for Relative Strength Index calculation."""

from datetime import UTC, datetime, timedelta

from price_analysis.rsi import compute_rsi


def series(values: list[float]) -> list[tuple[datetime, float]]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        (start + timedelta(days=index), value) for index, value in enumerate(values)
    ]


def test_rsi_reports_all_gains_as_100() -> None:
    assert compute_rsi(series([1, 2, 3, 4]), 3) == [
        {"date": "2026-01-04", "value": 100.0},
    ]


def test_rsi_reports_all_losses_as_zero() -> None:
    assert compute_rsi(series([4, 3, 2, 1]), 3) == [
        {"date": "2026-01-04", "value": 0.0},
    ]


def test_rsi_reports_flat_series_as_100() -> None:
    assert compute_rsi(series([5, 5, 5, 5]), 3) == [
        {"date": "2026-01-04", "value": 100.0},
    ]


def test_rsi_uses_wilder_smoothing_for_later_values() -> None:
    assert compute_rsi(series([10, 12, 11, 13, 12]), 2) == [
        {"date": "2026-01-03", "value": 66.6667},
        {"date": "2026-01-04", "value": 85.7143},
        {"date": "2026-01-05", "value": 54.5455},
    ]


def test_rsi_requires_period_plus_one_prices() -> None:
    assert compute_rsi(series([1, 2, 3]), 3) == []
