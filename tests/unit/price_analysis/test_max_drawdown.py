"""Tests for maximum drawdown calculation."""

from datetime import UTC, datetime, timedelta

import pytest

from price_analysis.max_drawdown import compute_max_drawdown


def series(values: list[float]) -> list[tuple[datetime, float]]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        (start + timedelta(days=index), value) for index, value in enumerate(values)
    ]


def test_drawdown_is_zero_for_monotonic_growth() -> None:
    assert compute_max_drawdown(series([1, 2, 3, 4])) == 0.0


def test_drawdown_reports_largest_peak_to_trough_decline() -> None:
    assert compute_max_drawdown(series([100, 120, 90, 110, 80])) == -33.33


def test_recovery_does_not_erase_prior_maximum_drawdown() -> None:
    assert compute_max_drawdown(series([100, 50, 100, 120])) == -50.0


def test_drawdown_of_empty_series_is_zero() -> None:
    assert compute_max_drawdown([]) == 0.0


@pytest.mark.parametrize("values", [[0, 0], [-1, -2], [-2, -1]])
def test_non_positive_prices_do_not_produce_drawdown(values: list[float]) -> None:
    assert compute_max_drawdown(series(values)) == 0.0


def test_drawdown_uses_new_peak_after_recovery() -> None:
    assert compute_max_drawdown(series([100, 50, 200, 100])) == -50.0
