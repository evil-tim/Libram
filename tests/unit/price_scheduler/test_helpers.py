# ruff: noqa: DTZ001
from datetime import datetime
from decimal import Decimal

from libram_types.libram_types import PriceRecord
from price_scheduler.service import (
    _day_has_missing_prices,
    _month_has_missing_prices,
    _month_range_for,
    _prev_month,
    _prev_week,
    _week_range_for,
)


def price(day):
    return PriceRecord(price=Decimal(1), timestamp=datetime.fromisoformat(day))


def test_month_range_and_previous_month_preserve_timezone():
    dt = datetime(2026, 3, 15, 12, tzinfo=datetime.now().astimezone().tzinfo)
    start, end = _month_range_for(dt)
    assert (start.month, start.day) == (3, 1)
    assert (end.month, end.day) == (4, 1)
    assert _prev_month(dt).month == 2


def test_month_missing_ignores_weekends_when_entity_does_not_have_them():
    start, end = datetime(2026, 8, 3), datetime(2026, 8, 10)
    assert (
        _month_has_missing_prices(
            [
                price("2026-08-03"),
                price("2026-08-04"),
                price("2026-08-05"),
                price("2026-08-06"),
                price("2026-08-07"),
            ],
            start,
            end,
            False,
        )
        is False
    )
    assert _month_has_missing_prices([price("2026-08-03")], start, end, False) is True
    assert (
        _month_has_missing_prices(
            [
                price("2026-08-03"),
                price("2026-08-04"),
                price("2026-08-05"),
                price("2026-08-06"),
                price("2026-08-07"),
            ],
            start,
            end,
            True,
        )
        is True
    )


def test_day_missing_skips_weekend_and_accepts_ohlc_start_timestamp():
    saturday = datetime(2026, 8, 8)
    assert _day_has_missing_prices([], saturday, datetime(2026, 8, 9), False) is False
    assert (
        _day_has_missing_prices([], datetime(2026, 8, 7), datetime(2026, 8, 8), False)
        is True
    )
    ohlc = PriceRecord(timestamp_start=datetime(2026, 8, 7, 9))
    assert (
        _day_has_missing_prices(
            [ohlc], datetime(2026, 8, 7), datetime(2026, 8, 8), False
        )
        is False
    )


def test_week_helpers_return_monday_to_monday_ranges():
    dt = datetime(2026, 8, 5)
    start, end = _week_range_for(dt)
    assert (start.weekday(), end.weekday()) == (0, 0)
    assert _prev_week(dt).weekday() == 0
