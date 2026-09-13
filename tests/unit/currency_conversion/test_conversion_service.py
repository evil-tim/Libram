"""Tests for the currency conversion service against a fake database."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from currency_conversion.conversion import DIRECT, INVERSE, NoRate
from currency_conversion.service import PHP, CurrencyConversionService
from libram_types.libram_types import DailyPrice, FxPath

OBSERVED_AT = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class FakeDatabase:
    """Stand-in for ``Database`` that reproduces the rate lookup's real semantics.

    ``get_price_at_or_before`` returns the most recent observation at or before
    the requested instant, so the no-look-ahead rule is genuinely exercised
    rather than assumed.
    """

    def __init__(self, entities=None, rates=None, series=None):
        self.entities = entities or {}
        self.rates = rates or {}
        self.series = series or {}

    def get_entity_by_id_raw(self, entity_id: UUID):
        return self.entities.get(entity_id)

    def get_price_at_or_before(self, entity_id: UUID, target: datetime):
        eligible = [
            (observed_at, value)
            for observed_at, value in self.rates.get(entity_id, [])
            if observed_at <= target
        ]
        if not eligible:
            return None
        return max(eligible, key=lambda pair: pair[0])[1]

    def query_daily_last_price(self, entity_id: UUID, start: datetime, end: datetime):
        return self.series.get(entity_id, [])


def entity(currency_id=None, code="USD") -> dict:
    return {"id": uuid4(), "code": code, "currency_id": currency_id}


def service(**kwargs) -> CurrencyConversionService:
    return CurrencyConversionService(FakeDatabase(**kwargs))


def test_resolve_prefers_direct_and_reports_the_rate_entity():
    usd = uuid4()
    # USD's own series is quoted in PHP.
    fx = service(entities={usd: entity(currency_id=None, code="USD")})
    assert fx.resolve(usd, None) == FxPath(rate_entity_id=usd, direction=DIRECT)
    assert fx.resolve(None, usd) == FxPath(rate_entity_id=usd, direction=INVERSE)


def test_resolve_returns_none_for_identity_and_for_unreachable_pairs():
    usd, usdc = uuid4(), uuid4()
    fx = service(
        entities={
            usd: entity(currency_id=None, code="USD"),
            usdc: entity(currency_id=None, code="USDC"),
        }
    )
    assert fx.resolve(usd, usd) is None
    # Both are quoted in PHP, so USDC -> USD would be two hops.
    assert fx.resolve(usdc, usd) is None


def test_resolve_follows_a_depth_two_pair():
    usdt, usdc = uuid4(), uuid4()
    fx = service(entities={usdt: entity(currency_id=usdc, code="USDT")})
    assert fx.resolve(usdt, usdc) == FxPath(rate_entity_id=usdt, direction=DIRECT)


def test_denomination_of_coerces_a_string_currency_id():
    usd = uuid4()
    fx = service(entities={usd: entity(currency_id=str(uuid4()))})
    assert isinstance(fx.denomination_of(usd), UUID)


def test_denomination_of_returns_none_for_an_unknown_entity():
    fx = service()
    assert fx.denomination_of(uuid4()) is None


def test_currency_exists_distinguishes_missing_from_php():
    usd = uuid4()
    fx = service(entities={usd: entity(currency_id=None)})
    assert fx.currency_exists(usd) is True
    assert fx.currency_exists(uuid4()) is False


def test_currency_code_labels_php_and_entity_currencies():
    usd = uuid4()
    fx = service(entities={usd: entity(currency_id=None, code="USD")})
    assert fx.currency_code(None) == PHP
    assert fx.currency_code(usd) == "USD"


def test_currency_code_rejects_an_unknown_entity():
    fx = service()
    with pytest.raises(ValueError):
        fx.currency_code(uuid4())


def test_rate_at_returns_the_stored_rate_and_coerces_it():
    usd = uuid4()
    fx = service(rates={usd: [(OBSERVED_AT, Decimal("57.25"))]})
    assert fx.rate_at(usd, datetime(2026, 1, 1, 13, 0, tzinfo=UTC)) == Decimal("57.25")

    # The OFX source stores its rate uncoerced, so a float can arrive.
    fx = service(rates={usd: [(OBSERVED_AT, 57.25)]})
    assert fx.rate_at(usd, datetime(2026, 1, 1, 13, 0, tzinfo=UTC)) == Decimal("57.25")


def test_rate_at_never_borrows_a_later_rate():
    usd = uuid4()
    earlier = Decimal(56)
    later = Decimal(58)
    fx = service(
        rates={
            usd: [
                (datetime(2026, 1, 1, 10, 0, tzinfo=UTC), earlier),
                (datetime(2026, 1, 1, 12, 0, tzinfo=UTC), later),
            ]
        }
    )
    # An instant between the two observations takes the earlier one, not the next.
    assert fx.rate_at(usd, datetime(2026, 1, 1, 11, 0, tzinfo=UTC)) == earlier
    assert fx.rate_at(usd, datetime(2026, 1, 1, 12, 0, tzinfo=UTC)) == later


def test_rate_at_raises_when_no_rate_precedes_the_instant():
    usd = uuid4()
    fx = service(rates={usd: [(OBSERVED_AT, Decimal(57))]})
    with pytest.raises(NoRate) as caught:
        fx.rate_at(usd, datetime(2026, 1, 1, 11, 0, tzinfo=UTC))
    # The failure carries what the caller needs to report it.
    assert caught.value.rate_entity_id == usd
    assert caught.value.at == datetime(2026, 1, 1, 11, 0, tzinfo=UTC)


def test_rate_series_is_keyed_by_utc_day():
    usd = uuid4()
    day = datetime(2026, 1, 1, tzinfo=UTC).date()
    fx = service(
        series={
            usd: [
                DailyPrice(
                    day=day,
                    observed_at=datetime(2026, 1, 1, tzinfo=UTC),
                    value=Decimal(57),
                )
            ]
        }
    )
    series = fx.rate_series(
        usd, datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
    )
    assert series == {day: Decimal(57)}


def test_rate_series_omits_days_without_an_observation():
    fx = service()
    assert (
        fx.rate_series(
            uuid4(), datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
        )
        == {}
    )
