"""Tests for pure currency path resolution and conversion arithmetic."""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from currency_conversion.conversion import (
    DIRECT,
    INVERSE,
    DenominationLookup,
    InvalidRate,
    convert,
    resolve_path,
)
from libram_types.libram_types import FxPath


def denominations(mapping: dict[UUID, UUID]) -> DenominationLookup:
    """Return a denomination lookup backed by ``mapping``, defaulting to PHP."""

    def lookup(currency_id: UUID):
        return mapping.get(currency_id)

    return lookup


def test_identity_when_source_and_target_are_the_same_currency():
    usd = uuid4()
    assert resolve_path(usd, usd, denominations({})) is None


def test_php_to_php_is_identity():
    assert resolve_path(None, None, denominations({})) is None


def test_direct_when_the_source_currency_is_quoted_in_the_target():
    # USD's own series is quoted in PHP, so USD -> PHP multiplies by that series.
    usd = uuid4()
    path = resolve_path(usd, None, denominations({}))
    assert path == FxPath(rate_entity_id=usd, direction=DIRECT)


def test_inverse_when_the_target_currency_is_quoted_in_the_source():
    # A PHP amount in a USD output currency divides by the USD series.
    usd = uuid4()
    path = resolve_path(None, usd, denominations({}))
    assert path == FxPath(rate_entity_id=usd, direction=INVERSE)


def test_direct_through_a_depth_two_pair():
    eur, usd = uuid4(), uuid4()
    path = resolve_path(eur, usd, denominations({eur: usd}))
    assert path == FxPath(rate_entity_id=eur, direction=DIRECT)


def test_direct_preferred_when_both_directions_resolve():
    a, b = uuid4(), uuid4()
    path = resolve_path(a, b, denominations({a: b, b: a}))
    assert path == FxPath(rate_entity_id=a, direction=DIRECT)


def test_no_path_for_a_two_hop_pair():
    # USDC and USD are both quoted in PHP, so USDC -> USD is two hops.
    usdc, usd = uuid4(), uuid4()
    assert resolve_path(usdc, usd, denominations({})) is None


def test_no_path_when_a_depth_two_currency_targets_php():
    # USDT is quoted in USDC, and USDC is quoted in PHP: USDT -> PHP is two hops.
    usdt, usdc = uuid4(), uuid4()
    assert resolve_path(usdt, None, denominations({usdt: usdc})) is None
    assert resolve_path(usdt, usdc, denominations({usdt: usdc})) == FxPath(
        rate_entity_id=usdt, direction=DIRECT
    )


def test_direct_conversion_multiplies():
    path = FxPath(rate_entity_id=uuid4(), direction=DIRECT)
    assert convert(Decimal("1200.19"), path, Decimal(57)) == Decimal("68410.830000")


def test_inverse_conversion_divides():
    path = FxPath(rate_entity_id=uuid4(), direction=INVERSE)
    assert convert(Decimal("68410.83"), path, Decimal(57)) == Decimal("1200.190000")


def test_converted_values_are_quantized_to_six_places_half_up():
    path = FxPath(rate_entity_id=uuid4(), direction=DIRECT)
    assert convert(Decimal(1), path, Decimal("0.1234567")) == Decimal("0.123457")
    assert convert(Decimal(1), path, Decimal("0.1234564")) == Decimal("0.123456")


def test_rates_are_coerced_before_arithmetic():
    # The OFX source stores its rate uncoerced, so a float can reach this call.
    path = FxPath(rate_entity_id=uuid4(), direction=DIRECT)
    assert convert(Decimal(2), path, 3.5) == Decimal("7.000000")


@pytest.mark.parametrize("rate", ["0", "-1", "-0.000001"])
def test_non_positive_rates_are_invalid(rate):
    path = FxPath(rate_entity_id=uuid4(), direction=DIRECT)
    with pytest.raises(InvalidRate):
        convert(Decimal(10), path, Decimal(rate))


def test_unknown_direction_is_rejected():
    path = FxPath(rate_entity_id=uuid4(), direction="sideways")
    with pytest.raises(ValueError):
        convert(Decimal(10), path, Decimal(57))
