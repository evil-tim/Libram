"""Currency conversion orchestration.

Binds the pure rules in :mod:`currency_conversion.conversion` to the database:
resolving a currency entity's own denomination, and reading the rate series that
supplies a conversion. No conversion arithmetic lives here.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from libram_database.db import Database
from libram_types.libram_types import FxPath

from .conversion import NoRate, resolve_path, to_decimal

#: The reporting label for the one currency with no entity.
PHP = "PHP"


class CurrencyConversionService:
    """Resolve single-hop conversion paths and the rates behind them."""

    def __init__(self, db: Database):
        self.db = db

    def resolve(self, from_id: UUID | None, to_id: UUID | None) -> FxPath | None:
        """Resolve a single-hop path between two currencies.

        Returns ``None`` both for identity (``from_id == to_id``) and for a pair
        with no single-hop route; callers distinguish them by comparing the ids.
        """
        return resolve_path(from_id, to_id, self.denomination_of)

    def denomination_of(self, currency_id: UUID) -> UUID | None:
        """Return the currency this currency entity's price series is quoted in.

        ``None`` means the series is quoted in PHP. An unknown entity also
        resolves to ``None``, so callers that must tell the two apart check
        :meth:`currency_exists` first.
        """
        entity = self.db.get_entity_by_id_raw(currency_id)
        if not entity:
            return None
        value = entity.get("currency_id")
        return UUID(str(value)) if value is not None else None

    def currency_exists(self, currency_id: UUID) -> bool:
        """Report whether an entity exists, for callers validating a currency id."""
        return self.db.get_entity_by_id_raw(currency_id) is not None

    def currency_code(self, currency_id: UUID | None) -> str:
        """Return the reporting label for a currency, e.g. ``USD`` or ``PHP``."""
        if currency_id is None:
            return PHP
        entity = self.db.get_entity_by_id_raw(currency_id)
        if not entity:
            raise ValueError(f"currency entity {currency_id} not found")
        return str(entity.get("code") or PHP)

    def rate_at(self, rate_entity_id: UUID, at: datetime) -> Decimal:
        """Return the rate observed at or before ``at``.

        Raises :class:`NoRate` when the series has no observation at or before the
        instant. A rate is never borrowed from after the reference instant, which
        is the whole point of passing the instant rather than a day.
        """
        rate = self.db.get_price_at_or_before(rate_entity_id, at)
        if rate is None:
            raise NoRate(rate_entity_id, at)
        return to_decimal(rate)

    def rate_series(
        self, rate_entity_id: UUID, start: datetime, end: datetime
    ) -> dict[date, Decimal]:
        """Return one rate per UTC calendar day in ``[start, end)``.

        Days with no observation are absent from the mapping, so a consumer can
        tell a missing day from a present one without a second query.
        """
        return {
            row.day: to_decimal(row.value)
            for row in self.db.query_daily_last_price(rate_entity_id, start, end)
        }


__all__ = ["PHP", "CurrencyConversionService"]
