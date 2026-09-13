"""Adapter converting the records returned by ``GET /api/v1/prices``.

The caller may name an output currency, in which case every monetary field of
every returned record is converted into it at that record's own instant.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict
from datetime import datetime
from typing import Any
from uuid import UUID

from libram_types.libram_types import FxPath, PriceRecord

from .conversion import NoPath, NoRate, convert
from .service import CurrencyConversionService

#: The literal a caller passes to request the one currency with no entity.
PHP_SENTINEL = "PHP"

#: ``PriceRecord`` fields that carry money.
MONETARY_FIELDS = ("price", "open", "high", "low", "close")


def parse_quote_currency(value: str | None) -> UUID | None:
    """Parse the ``quote_currency_id`` request parameter.

    Returns ``None`` for an omitted parameter or the ``PHP`` sentinel, a UUID for
    a currency entity, and raises ``ValueError`` for anything else -- including a
    blank value, which is a caller bug rather than a request for PHP.

    A currency *code* is deliberately not accepted, even though it reads
    naturally: codes are unique only per datasource and two entities are named
    ``USD``, so code lookup would be ambiguous by construction.
    """
    if value is None:
        return None
    text = value.strip()
    if text.upper() == PHP_SENTINEL:
        return None
    try:
        return UUID(text)
    except ValueError as exc:
        raise ValueError(
            f"quote_currency_id must be a currency entity UUID or {PHP_SENTINEL!r}, got {value!r}"
        ) from exc


def reference_instant(record: PriceRecord) -> datetime | None:
    """Return the instant a record is valued at.

    That is ``COALESCE(timestamp, timestamp_start)``: a point row's own
    timestamp, or a bar's opening instant. Deliberately not the end of the
    record's UTC day, which would let a rate observed later in the day convert an
    earlier value.
    """
    return record.timestamp or record.timestamp_start


def convert_price_records(
    records: Iterable[PriceRecord],
    source_currency_id: UUID | None,
    quote_currency_id: UUID | None,
    fx: CurrencyConversionService,
) -> list[dict[str, Any]]:
    """Convert every monetary field of every record into ``quote_currency_id``.

    ``None`` means PHP on either side. Each record converts at its own instant,
    so the applied rate is always observed at or before the value it converts.

    Raises :class:`NoPath` when the source currency has no single-hop route to
    the requested one, and propagates :class:`NoRate` / :class:`InvalidRate` when
    a record's rate is missing or unusable.
    """
    identity = source_currency_id == quote_currency_id
    path: FxPath | None = (
        None if identity else fx.resolve(source_currency_id, quote_currency_id)
    )
    if not identity and path is None:
        raise NoPath(
            f"no single-hop conversion from currency {source_currency_id} to {quote_currency_id}"
        )

    target_label = fx.currency_code(quote_currency_id)
    source_label = None if path is None else fx.currency_code(source_currency_id)

    converted: list[dict[str, Any]] = []
    for record in records:
        payload = asdict(record)
        payload["currency"] = target_label
        payload["conversion"] = None
        if path is not None:
            at = reference_instant(record)
            if at is None:
                raise NoRate(
                    path.rate_entity_id,
                    None,
                    message="price record has no timestamp to value a conversion at",
                )
            rate = fx.rate_at(path.rate_entity_id, at)
            for field in MONETARY_FIELDS:
                if payload[field] is not None:
                    payload[field] = convert(payload[field], path, rate)
            payload["conversion"] = {
                "from": source_label,
                "rate": rate,
                "direction": path.direction,
            }
        converted.append(payload)
    return converted


__all__ = [
    "MONETARY_FIELDS",
    "PHP_SENTINEL",
    "convert_price_records",
    "parse_quote_currency",
    "reference_instant",
]
