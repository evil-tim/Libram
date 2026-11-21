"""Tests for the pure fusion module, with literal inputs.

No database, no FastAPI, no currency module: conversion is a plain function, so
every rule in the fused-entity plan's read path is asserted directly. What is
*not* here is conversion arithmetic and path resolution -- those belong to the
currency module's own suite, and fusion only sees the result.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

import pytest

from libram_types.libram_types import DailyPrice
from price_analysis.fusion import (
    MEMBERSHIP_ALL,
    MEMBERSHIP_STRONG,
    STRONG,
    WEAK,
    WEAK_EXCLUDED_BY_STRONG,
    ConversionDetail,
    Converted,
    FusableMember,
    MemberSeries,
    fuse_daily,
    select_members,
)

JAN_1 = date(2026, 1, 1)
JAN_2 = date(2026, 1, 2)
JAN_3 = date(2026, 1, 3)
JAN_4 = date(2026, 1, 4)


def member(
    code: str = "BTC",
    datasource: str = "coindesk-ohlc-json",
    strength: str = STRONG,
) -> FusableMember:
    return FusableMember(
        entity_id=uuid5(NAMESPACE_URL, f"{code}:{datasource}"),
        entity_code=code,
        datasource=datasource,
        strength=strength,
    )


def price(day: date, value: str, hour: int = 23) -> DailyPrice:
    return DailyPrice(
        day=day,
        observed_at=datetime(day.year, day.month, day.day, hour, 59, tzinfo=UTC),
        value=Decimal(value),
    )


def identity(member_series, value, day) -> Converted:
    """A conversion that only renames the quantity, as a same-currency member's does."""
    return Converted(value=value)


def series(member_, *prices: DailyPrice) -> MemberSeries:
    return MemberSeries(member=member_, prices=list(prices))


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------
def test_strong_mode_admits_only_strong_members_and_names_the_reason():
    strong = member("BTC", "kraken-ticker-rest-json")
    weak = member("WBTC", "web3-uniswap-arbitrum", strength=WEAK)

    included, excluded = select_members([strong, weak], MEMBERSHIP_STRONG)

    assert included == [strong]
    assert [(item.member.entity_code, item.reason) for item in excluded] == [
        ("WBTC", WEAK_EXCLUDED_BY_STRONG)
    ]


def test_all_mode_admits_weak_members_too():
    strong = member("BTC", "kraken-ticker-rest-json")
    weak = member("WBTC", "web3-uniswap-arbitrum", strength=WEAK)

    included, excluded = select_members([strong, weak], MEMBERSHIP_ALL)

    assert included == [strong, weak]
    assert excluded == []


def test_selection_is_empty_for_a_group_with_only_weak_members():
    # The service turns this into 422 empty_membership rather than returning an
    # empty series, so it must be observable here.
    weak = member("WBTC", "web3-uniswap-arbitrum", strength=WEAK)

    included, excluded = select_members([weak], MEMBERSHIP_STRONG)

    assert included == []
    assert len(excluded) == 1


def test_selection_rejects_an_unknown_mode():
    with pytest.raises(ValueError, match="unknown membership mode"):
        select_members([member()], "any")


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------
def test_median_of_three_contributors():
    bars = fuse_daily(
        [
            series(member("BTC", "a"), price(JAN_1, "68420")),
            series(member("BTC", "b"), price(JAN_1, "68411")),
            series(member("BTC", "c"), price(JAN_1, "99999")),
        ],
        JAN_1,
        JAN_2,
        identity,
    )

    assert len(bars) == 1
    assert bars[0].value == Decimal("68420.000000")
    assert len(bars[0].sources) == 3


def test_two_contributors_use_their_mean_and_quantize_half_up():
    # The median of an even count is the mean of the middle pair, which can land
    # one decimal place past six; the bar reports six.
    bars = fuse_daily(
        [
            series(member("BTC", "a"), price(JAN_1, "1.000000")),
            series(member("BTC", "b"), price(JAN_1, "2.000001")),
        ],
        JAN_1,
        JAN_2,
        identity,
    )

    assert bars[0].value == Decimal("1.500001")


def test_one_contributor_is_returned_unchanged():
    bars = fuse_daily(
        [series(member(), price(JAN_1, "68420.123456"))], JAN_1, JAN_2, identity
    )

    assert bars[0].value == Decimal("68420.123456")
    assert len(bars[0].sources) == 1


# ---------------------------------------------------------------------------
# conversion
# ---------------------------------------------------------------------------
def test_conversion_is_applied_once_per_member_day():
    calls = []

    def convert(member_series, value, day):
        calls.append((member_series.member.entity_code, value, day))
        return Converted(
            value=value * 57,
            conversion=ConversionDetail(
                currency="USD",
                value=value,
                rate=Decimal(57),
                direction="direct",
            ),
        )

    bars = fuse_daily(
        [
            series(member("BTC", "a"), price(JAN_1, "1200"), price(JAN_2, "1300")),
            series(member("BTC", "b"), price(JAN_1, "1210")),
        ],
        JAN_1,
        JAN_3,
        convert,
    )

    assert len(calls) == 3
    assert bars[0].value == Decimal("68685.000000")
    assert bars[1].value == Decimal("74100.000000")


def test_a_converted_contribution_reports_the_arithmetic_it_underwent():
    conversion = ConversionDetail(
        currency="USD", value=Decimal(1200), rate=Decimal(57), direction="direct"
    )

    bars = fuse_daily(
        [series(member(), price(JAN_1, "1200"))],
        JAN_1,
        JAN_2,
        lambda member_series, value, day: Converted(value * 57, conversion),
    )

    source = bars[0].sources[0]
    assert source.value == Decimal(68400)
    assert source.converted_from == conversion


def test_a_member_already_in_the_output_currency_reports_no_conversion():
    bars = fuse_daily([series(member(), price(JAN_1, "68420"))], JAN_1, JAN_2, identity)

    assert bars[0].sources[0].converted_from is None


def test_a_conversion_failure_fails_the_request():
    # Decision 8: an unconvertible value is a contract violation, never a member
    # quietly dropped from the day.
    def convert(member_series, value, day):
        raise RuntimeError("no path")

    with pytest.raises(RuntimeError, match="no path"):
        fuse_daily([series(member(), price(JAN_1, "1"))], JAN_1, JAN_2, convert)


# ---------------------------------------------------------------------------
# days
# ---------------------------------------------------------------------------
def test_a_member_with_no_observation_that_day_is_skipped():
    calls = []

    def convert(member_series, value, day):
        calls.append(member_series.member.datasource)
        return Converted(value)

    bars = fuse_daily(
        [
            series(member("BTC", "a"), price(JAN_1, "10"), price(JAN_2, "20")),
            series(member("BTC", "b"), price(JAN_2, "30")),
        ],
        JAN_1,
        JAN_3,
        convert,
    )

    assert [bar.day for bar in bars] == [JAN_1, JAN_2]
    assert [len(bar.sources) for bar in bars] == [1, 2]
    # The absent member is not converted for the day it did not observe.
    assert calls == ["a", "a", "b"]


def test_a_day_no_member_observed_is_absent_never_empty():
    bars = fuse_daily(
        [
            series(member("BTC", "a"), price(JAN_1, "10"), price(JAN_3, "10")),
        ],
        JAN_1,
        JAN_4,
        identity,
    )

    assert [bar.day for bar in bars] == [JAN_1, JAN_3]


def test_days_outside_the_requested_range_are_ignored():
    bars = fuse_daily(
        [
            series(
                member(),
                price(date(2025, 12, 31), "1"),
                price(JAN_1, "10"),
                # end is exclusive, so this day is not part of the range.
                price(JAN_2, "20"),
            )
        ],
        JAN_1,
        JAN_2,
        identity,
    )

    assert [bar.day for bar in bars] == [JAN_1]
    assert bars[0].value == Decimal("10.000000")


def test_bars_are_ordered_by_day_even_when_series_are_not():
    bars = fuse_daily(
        [
            series(member("BTC", "b"), price(JAN_2, "20"), price(JAN_1, "10")),
            series(member("BTC", "a"), price(JAN_3, "30")),
        ],
        JAN_1,
        JAN_4,
        identity,
    )

    assert [bar.day for bar in bars] == [JAN_1, JAN_2, JAN_3]


def test_an_empty_range_emits_no_bars():
    assert fuse_daily([series(member())], JAN_1, JAN_2, identity) == []


def test_an_inverted_range_is_rejected():
    with pytest.raises(ValueError, match="empty or inverted range"):
        fuse_daily([], JAN_2, JAN_1, identity)


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------
def test_sources_are_ordered_by_entity_code_then_datasource():
    # Insertion order is deliberately the reverse of the response order.
    bars = fuse_daily(
        [
            series(member("WBTC", "web3-uniswap-arbitrum"), price(JAN_1, "1")),
            series(member("BTC", "kraken-ticker-rest-json"), price(JAN_1, "2")),
            series(member("BTC", "chainlink-ethereum"), price(JAN_1, "3")),
        ],
        JAN_1,
        JAN_2,
        identity,
    )

    assert [(s.entity_code, s.datasource) for s in bars[0].sources] == [
        ("BTC", "chainlink-ethereum"),
        ("BTC", "kraken-ticker-rest-json"),
        ("WBTC", "web3-uniswap-arbitrum"),
    ]


def test_a_source_carries_the_observation_it_came_from():
    observed = price(JAN_1, "68420", hour=17)

    bars = fuse_daily([series(member(), observed)], JAN_1, JAN_2, identity)

    assert bars[0].sources[0].observed_at == observed.observed_at
