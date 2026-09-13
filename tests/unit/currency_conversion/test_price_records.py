"""Tests for the ``GET /api/v1/prices`` conversion adapter."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from currency_conversion.conversion import DIRECT, INVERSE, InvalidRate, NoPath, NoRate
from currency_conversion.price_records import (
    convert_price_records,
    parse_quote_currency,
    reference_instant,
)
from libram_types.libram_types import FxPath, PriceRecord

UTC_DAY = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
LATE = datetime(2026, 1, 1, 23, 59, tzinfo=UTC)


class FakeConversionService:
    """Stand-in for ``CurrencyConversionService`` recording what it was asked."""

    def __init__(self, path=None, rate=Decimal(57), codes=None, error=None):
        self.path = path
        self.rate = rate
        self.codes = codes or {}
        self.error = error
        self.instants: list[datetime] = []
        self.resolve_calls = 0

    def resolve(self, from_id, to_id):
        self.resolve_calls += 1
        return self.path

    def rate_at(self, rate_entity_id, at):
        self.instants.append(at)
        if self.error is not None:
            raise self.error
        return self.rate

    def currency_code(self, currency_id):
        return self.codes.get(currency_id, "PHP")


def point_row(price="1200.19", at=LATE) -> PriceRecord:
    return PriceRecord(price=Decimal(price), timestamp=at)


def bar_row(open_="1140", high="1210", low="1135", close="1200.19") -> PriceRecord:
    return PriceRecord(
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        timestamp_start=UTC_DAY,
        timestamp_end=datetime(2026, 1, 2, tzinfo=UTC),
    )


def test_parse_accepts_an_entity_uuid():
    currency_id = uuid4()
    assert parse_quote_currency(str(currency_id)) == currency_id


@pytest.mark.parametrize("value", [None, "PHP", "php", " Php "])
def test_parse_treats_omitted_and_php_as_the_entity_less_currency(value):
    assert parse_quote_currency(value) is None


@pytest.mark.parametrize("value", ["USD", "not-a-uuid", "12345", "", "   "])
def test_parse_rejects_blank_values_codes_and_junk(value):
    with pytest.raises(ValueError):
        parse_quote_currency(value)


def test_reference_instant_uses_the_records_own_timestamp():
    assert reference_instant(point_row(at=LATE)) == LATE
    assert reference_instant(bar_row()) == UTC_DAY


def test_reference_instant_prefers_a_point_timestamp_over_a_bar_start():
    # COALESCE(timestamp, timestamp_start): if a row somehow carries both, the
    # point timestamp is the effective instant.
    record = PriceRecord(price=Decimal(1), timestamp=LATE, timestamp_start=UTC_DAY)
    assert reference_instant(record) == LATE


def test_point_row_price_is_converted_and_the_arithmetic_is_reported():
    usd = uuid4()
    fx = FakeConversionService(
        path=FxPath(rate_entity_id=usd, direction=DIRECT),
        codes={usd: "USD"},
    )
    records = convert_price_records([point_row()], usd, None, fx)
    assert len(records) == 1
    record = records[0]
    assert record["price"] == Decimal("68410.830000")
    assert record["currency"] == "PHP"
    assert record["conversion"] == {
        "from": "USD",
        "rate": Decimal(57),
        "direction": DIRECT,
    }


def test_every_ohlc_field_converts_by_the_same_factor_and_keeps_invariants():
    usd = uuid4()
    fx = FakeConversionService(
        path=FxPath(rate_entity_id=usd, direction=DIRECT),
        codes={usd: "USD"},
    )
    record = convert_price_records([bar_row()], usd, None, fx)[0]
    assert record["open"] == Decimal("64980.000000")
    assert record["high"] == Decimal("68970.000000")
    assert record["low"] == Decimal("64695.000000")
    assert record["close"] == Decimal("68410.830000")
    assert record["low"] <= min(record["open"], record["close"])
    assert record["high"] >= max(record["open"], record["close"])


def test_point_row_leaves_absent_bar_fields_untouched():
    usd = uuid4()
    fx = FakeConversionService(
        path=FxPath(rate_entity_id=usd, direction=DIRECT),
        codes={usd: "USD"},
    )
    record = convert_price_records([point_row()], usd, None, fx)[0]
    assert record["open"] is None
    assert record["high"] is None
    assert record["low"] is None
    assert record["close"] is None


def test_each_record_converts_at_its_own_instant():
    usd = uuid4()
    fx = FakeConversionService(
        path=FxPath(rate_entity_id=usd, direction=DIRECT),
        codes={usd: "USD"},
    )
    convert_price_records([point_row(at=LATE), bar_row()], usd, None, fx)
    assert fx.instants == [LATE, UTC_DAY]


def test_inverse_conversion_divides():
    usd = uuid4()
    fx = FakeConversionService(
        path=FxPath(rate_entity_id=usd, direction=INVERSE),
        codes={usd: "USD"},
    )
    record = convert_price_records([point_row(price="68410.83")], None, usd, fx)[0]
    assert record["price"] == Decimal("1200.190000")
    assert record["currency"] == "USD"
    assert record["conversion"]["from"] == "PHP"


def test_records_already_in_the_requested_currency_are_marked_unconverted():
    usd = uuid4()
    fx = FakeConversionService(codes={usd: "USD"})
    record = convert_price_records([point_row()], usd, usd, fx)[0]
    assert record["price"] == Decimal("1200.19")
    assert record["currency"] == "USD"
    assert record["conversion"] is None
    assert fx.resolve_calls == 0
    assert fx.instants == []


def test_no_path_is_raised_when_the_pair_has_no_single_hop_route():
    usdc, usd = uuid4(), uuid4()
    fx = FakeConversionService(path=None, codes={usdc: "USDC", usd: "USD"})
    with pytest.raises(NoPath):
        convert_price_records([point_row()], usdc, usd, fx)


def test_missing_and_invalid_rates_propagate_with_their_context():
    usd = uuid4()
    path = FxPath(rate_entity_id=usd, direction=DIRECT)
    with pytest.raises(NoRate) as caught:
        convert_price_records(
            [point_row()],
            usd,
            None,
            FakeConversionService(path=path, error=NoRate(usd, LATE)),
        )
    # The caller needs to report which pair failed and at what instant.
    assert caught.value.rate_entity_id == usd
    assert caught.value.at == LATE

    with pytest.raises(InvalidRate) as invalid:
        convert_price_records(
            [point_row()], usd, None, FakeConversionService(path=path, rate=Decimal(0))
        )
    assert invalid.value.rate_entity_id == usd


def test_an_empty_page_needs_no_rate_lookup():
    usd = uuid4()
    fx = FakeConversionService(
        path=FxPath(rate_entity_id=usd, direction=DIRECT),
        codes={usd: "USD"},
    )
    assert convert_price_records([], usd, None, fx) == []
    assert fx.instants == []


def test_an_empty_page_with_no_path_still_reports_the_path_failure():
    usdc, usd = uuid4(), uuid4()
    fx = FakeConversionService(path=None, codes={usdc: "USDC", usd: "USD"})
    with pytest.raises(NoPath):
        convert_price_records([], usdc, usd, fx)


def test_a_record_without_a_timestamp_cannot_be_converted():
    usd = uuid4()
    fx = FakeConversionService(
        path=FxPath(rate_entity_id=usd, direction=DIRECT), codes={usd: "USD"}
    )
    with pytest.raises(NoRate):
        convert_price_records([PriceRecord(price=Decimal(1))], usd, None, fx)


def test_parsing_returns_a_uuid_instance():
    assert isinstance(parse_quote_currency(str(uuid4())), UUID)
