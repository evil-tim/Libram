"""Pure membership selection and daily fusion for entity groups.

Phase 2 of ``docs/fused_entity_data_plan.md``. Nothing here knows about the
database, HTTP, or currency resolution: a member arrives already reduced to one
value per UTC day, and conversion arrives as an injected callable. That is what
makes the whole algorithm testable with literal values.

Two rules shape it. A member contributes at most one value per day (its last
observation of that day), and a selected member that holds a value but cannot be
converted fails the request rather than being dropped -- an absent observation is
the missing data this feature exists to smooth over, an unconvertible one is a
contract violation.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Context, Decimal
from statistics import median
from uuid import UUID

from libram_types.libram_types import DailyPrice

#: The actual reference, e.g. BTC from a major exchange or an oracle.
STRONG = "strong"

#: A derivative or wrapped representation, e.g. WBTC.
WEAK = "weak"

MEMBERSHIP_STRONG = "strong"
MEMBERSHIP_ALL = "all"

#: The two ways a caller may widen the selection.
VALID_MEMBERSHIP_MODES: tuple[str, ...] = (MEMBERSHIP_STRONG, MEMBERSHIP_ALL)

#: The one exclusion reason strong mode reports.
WEAK_EXCLUDED_BY_STRONG = "weak_member_excluded_by_strong_mode"

#: Fused values are reported at the precision conversions already use. Deliberately
#: a local constant rather than an import: fusion stays independent of the currency
#: module, and this is a reporting precision, not conversion arithmetic.
FUSED_QUANTUM = Decimal("0.000001")

#: Quantization runs wider than the default context so a large magnitude cannot
#: make ``quantize`` raise ``InvalidOperation``.
QUANTIZE_CONTEXT = Context(prec=60, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class FusableMember:
    """One group member reduced to the fields fusion needs.

    ``currency_id`` is the currency the member's own series is quoted in, and
    ``None`` means PHP, matching ``entity.currency_id``. It is not read here;
    the injected conversion callable owns that decision.
    """

    entity_id: UUID
    entity_code: str
    datasource: str
    strength: str
    currency_id: UUID | None = None


@dataclass(frozen=True)
class MemberSeries:
    """A member and its daily reduction, at most one ``DailyPrice`` per day."""

    member: FusableMember
    prices: Sequence[DailyPrice] = ()


@dataclass(frozen=True)
class ConversionDetail:
    """The arithmetic a conversion applied to one contribution.

    Reported so a converted contribution is never indistinguishable from a
    directly observed one: ``currency`` and ``value`` are the raw observation,
    ``rate`` is the stored rate, and ``direction`` is how it was applied --
    ``direct`` multiplies, ``inverse`` divides.
    """

    currency: str
    value: Decimal
    rate: Decimal
    direction: str


@dataclass(frozen=True)
class Converted:
    """One member-day value in the group's output currency.

    ``conversion`` is ``None`` when the member is already quoted in that
    currency, which is not the same as a conversion that did nothing.
    """

    value: Decimal
    conversion: ConversionDetail | None = None


@dataclass(frozen=True)
class ExcludedMember:
    """A member the resolved membership did not admit, and why."""

    member: FusableMember
    reason: str


@dataclass(frozen=True)
class FusedSource:
    """One member's contribution to one fused day."""

    entity_code: str
    datasource: str
    value: Decimal
    observed_at: datetime
    converted_from: ConversionDetail | None = None


@dataclass(frozen=True)
class FusedBar:
    """One UTC calendar day's fused value, with the sources that produced it.

    ``sources`` is the contributor count: a day built from one feed says so.
    """

    day: date
    value: Decimal
    sources: tuple[FusedSource, ...]


#: ``convert(series, value, day)`` returns the value in the group's currency.
#: It may raise anything; fusion does not swallow or reinterpret failures.
Convert = Callable[[MemberSeries, Decimal, date], Converted]


def select_members(
    members: Iterable[FusableMember],
    mode: str = MEMBERSHIP_STRONG,
) -> tuple[list[FusableMember], list[ExcludedMember]]:
    """Split members into the included and the excluded for ``mode``.

    ``strong`` admits only ``strength = 'strong'``; ``all`` admits weak members
    too. Selection is closed-world: a member is admitted by an explicit row and
    by nothing else. Input order is preserved in both lists.
    """
    if mode not in VALID_MEMBERSHIP_MODES:
        raise ValueError(
            f"unknown membership mode: {mode!r}; expected one of "
            f"{list(VALID_MEMBERSHIP_MODES)}"
        )

    included: list[FusableMember] = []
    excluded: list[ExcludedMember] = []
    for member in members:
        if mode == MEMBERSHIP_ALL or member.strength == STRONG:
            included.append(member)
        else:
            excluded.append(ExcludedMember(member, WEAK_EXCLUDED_BY_STRONG))
    return included, excluded


def fuse_daily(
    series: Sequence[MemberSeries],
    start: date,
    end: date,
    convert: Convert,
) -> list[FusedBar]:
    """Reduce included members' daily series to one median value per day.

    Days come only from the data: a day no member observed is absent, never
    emitted empty. ``[start, end)`` bounds the days considered, so a series that
    carries a neighbouring day does not leak into the response.

    For each day, every member holding a value is converted and contributes;
    a member with no observation that day is skipped. Aggregation is the plain
    median of the converted values, so a single outlier print cannot move a bar
    with three or more contributors. With exactly two contributors the median is
    their mean -- the ``sources`` list is the disclosure, not the aggregation.

    ``convert`` is called once per member-day that has a value, and any exception
    it raises propagates: an unconvertible contribution fails the request.

    ``series`` is expected to hold one entry per member. A member repeated in it
    would contribute twice to every day it observed, so the caller must not pass
    duplicates -- the membership table's ``PRIMARY KEY (group_id, entity_id)`` is
    what guarantees that upstream.
    """
    if start >= end:
        raise ValueError(f"empty or inverted range: start={start} end={end}")

    # A member's own series is already one row per day; a repeated day would mean
    # the read broke its contract, so the last one wins deterministically.
    by_day: dict[UUID, dict[date, DailyPrice]] = {
        member_series.member.entity_id: {
            price.day: price
            for price in member_series.prices
            if start <= price.day < end
        }
        for member_series in series
    }

    days = sorted({day for member_days in by_day.values() for day in member_days})
    bars: list[FusedBar] = []
    for day in days:
        contributors: list[FusedSource] = []
        for member_series in series:
            price = by_day[member_series.member.entity_id].get(day)
            if price is None:
                continue
            converted = convert(member_series, price.value, day)
            contributors.append(
                FusedSource(
                    entity_code=member_series.member.entity_code,
                    datasource=member_series.member.datasource,
                    value=converted.value,
                    observed_at=price.observed_at,
                    converted_from=converted.conversion,
                )
            )
        if not contributors:
            continue
        # Ordered by entity code, then datasource: the same order membership is
        # listed in, and stable without relying on insertion order.
        contributors.sort(key=lambda source: (source.entity_code, source.datasource))
        bars.append(
            FusedBar(
                day=day,
                value=_median([source.value for source in contributors]),
                sources=tuple(contributors),
            )
        )
    return bars


def _median(values: Sequence[Decimal]) -> Decimal:
    """Median of already-converted values, quantized to six decimals.

    An even contributor count halves the middle pair, which can land one decimal
    place past six; the bar reports at the precision its contributors were
    converted at, so it never implies more precision than it has.
    """
    return median(values).quantize(
        FUSED_QUANTUM, rounding=ROUND_HALF_UP, context=QUANTIZE_CONTEXT
    )


__all__ = [
    "FUSED_QUANTUM",
    "MEMBERSHIP_ALL",
    "MEMBERSHIP_STRONG",
    "QUANTIZE_CONTEXT",
    "STRONG",
    "VALID_MEMBERSHIP_MODES",
    "WEAK",
    "WEAK_EXCLUDED_BY_STRONG",
    "ConversionDetail",
    "Convert",
    "Converted",
    "ExcludedMember",
    "FusableMember",
    "FusedBar",
    "FusedSource",
    "MemberSeries",
    "fuse_daily",
    "select_members",
]
