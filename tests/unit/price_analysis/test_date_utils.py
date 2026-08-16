"""Tests for date and timezone utilities."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfoNotFoundError

import pytest

from price_analysis.date_utils import convert_to_timezone_aware


def test_convert_to_timezone_aware_applies_iana_timezone() -> None:
    result = convert_to_timezone_aware("2026-01-02T03:04:05", "Asia/Manila")

    assert result == datetime(2026, 1, 2, 3, 4, 5, tzinfo=result.tzinfo)
    assert result.utcoffset().total_seconds() == 8 * 60 * 60


def test_convert_to_timezone_aware_rejects_invalid_date() -> None:
    with pytest.raises(ValueError):
        convert_to_timezone_aware("2026-02-30T03:04:05", "UTC")


def test_convert_to_timezone_aware_rejects_invalid_timezone() -> None:
    with pytest.raises(ZoneInfoNotFoundError):
        convert_to_timezone_aware("2026-01-02T03:04:05", "Not/A_Timezone")


def test_convert_to_timezone_aware_requires_expected_format() -> None:
    with pytest.raises(ValueError):
        convert_to_timezone_aware("2026-01-02", "UTC")


def test_convert_to_timezone_aware_preserves_wall_clock_time() -> None:
    result = convert_to_timezone_aware("2026-07-01T12:30:00", "UTC")

    assert result.replace(tzinfo=None) == datetime(
        2026, 7, 1, 12, 30, tzinfo=UTC
    ).replace(tzinfo=None)
    assert result.tzinfo is not None


@pytest.mark.parametrize(
    "value, expected",
    [("2026-01-01T00:00:00", "2026-01-01"), ("2026-12-31T23:59:59", "2026-12-31")],
)
def test_date_conversion_accepts_boundary_timestamps(value: str, expected: str) -> None:
    assert convert_to_timezone_aware(value, "UTC").date().isoformat() == expected


__all__ = []
