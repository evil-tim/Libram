"""Pure currency resolution and conversion.

Nothing here knows about the database or HTTP. Path resolution receives each
currency's own denomination through an injected callable, and the rate arrives
as an argument, so every rule is testable with literal values.

A currency is identified by the id of the entity representing it, per
``docs/currency_conversion_plan.md``. ``None`` means PHP, the one currency with
no entity, and an entity's own price series is quoted "per one of itself" in the
currency named by its own ``currency_id``.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from libram_types.libram_types import FxPath

DIRECT = "direct"
INVERSE = "inverse"

#: Converted values are rounded to the precision the OFX request already asks for.
CONVERTED_QUANTUM = Decimal("0.000001")

#: A rate of zero or less cannot be applied in either direction.
MINIMUM_RATE = Decimal(0)

#: Every currency id is an entity id; ``None`` is PHP.
type CurrencyId = UUID | None

#: ``denomination_of(currency_id)`` returns the currency that entity's own price
#: series is quoted in, or ``None`` when it is quoted in PHP.
DenominationLookup = Callable[[UUID], CurrencyId]


class CurrencyConversionError(Exception):
    """Base class for the failures this module reports."""


class NoPath(CurrencyConversionError):
    """No single-hop route exists between the two currencies."""


class NoRate(CurrencyConversionError):
    """No rate exists at or before the reference instant."""


class InvalidRate(CurrencyConversionError):
    """A rate is present but is not positive."""


def resolve_path(
    from_id: CurrencyId,
    to_id: CurrencyId,
    denomination_of: DenominationLookup,
) -> FxPath | None:
    """Resolve a single-hop conversion between two currencies.

    A direct conversion exists when the source currency's own entity is quoted
    in the target currency. An inverse conversion exists when the target
    currency's own entity is quoted in the source currency. Anything else --
    including every two-hop route, such as USDC to USD by way of PHP -- has no
    path.

    ``None`` is returned both for identity (``from_id == to_id``) and for a pair
    with no route; callers tell them apart by comparing the currency ids.
    """
    if from_id == to_id:
        return None
    if from_id is not None and denomination_of(from_id) == to_id:
        return FxPath(rate_entity_id=from_id, direction=DIRECT)
    if to_id is not None and denomination_of(to_id) == from_id:
        return FxPath(rate_entity_id=to_id, direction=INVERSE)
    return None


def _as_decimal(value: object) -> Decimal:
    """Coerce a stored numeric to ``Decimal``.

    The OFX source stores its rate uncoerced, so a rate can arrive as a float.
    """
    return value if isinstance(value, Decimal) else Decimal(str(value))


def convert(value: object, path: FxPath, rate: object) -> Decimal:
    """Apply ``path`` to ``value`` at ``rate``, quantized to six decimals.

    Direct conversion multiplies and inverse conversion divides, so there is a
    single rounding step in either direction rather than an intermediate
    reciprocal.
    """
    amount = _as_decimal(value)
    rate_decimal = _as_decimal(rate)
    if rate_decimal <= MINIMUM_RATE:
        raise InvalidRate(
            f"rate for currency entity {path.rate_entity_id} is not positive: {rate_decimal}"
        )
    if path.direction == DIRECT:
        converted = amount * rate_decimal
    elif path.direction == INVERSE:
        converted = amount / rate_decimal
    else:
        raise ValueError(f"unknown conversion direction: {path.direction!r}")
    return converted.quantize(CONVERTED_QUANTUM, rounding=ROUND_HALF_UP)


__all__ = [
    "CONVERTED_QUANTUM",
    "DIRECT",
    "INVERSE",
    "CurrencyConversionError",
    "DenominationLookup",
    "InvalidRate",
    "NoPath",
    "NoRate",
    "convert",
    "resolve_path",
]
