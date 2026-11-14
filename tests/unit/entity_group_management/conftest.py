"""Shared fakes for the entity-group suite.

Conversion is faked as *data*, not as rules: ``resolve`` delegates to the real
``resolve_path``, so a test that says "no path" is exercising the currency
module's actual single-hop rule rather than a stub that agreed with the service.
Only the inputs that live in the database -- each currency entity's own
denomination, the stored rate series, and the currency labels -- are supplied
here.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from currency_conversion.conversion import resolve_path
from currency_conversion.service import PHP


class FakeCurrencyConversion:
    """Stand-in for ``CurrencyConversionService`` backed by literal data."""

    def __init__(
        self,
        denominations: dict[UUID, UUID | None] | None = None,
        rates: dict[UUID, dict[date, Decimal]] | None = None,
        labels: dict[UUID, str] | None = None,
    ) -> None:
        self.denominations = dict(denominations or {})
        self.rates = {key: dict(series) for key, series in (rates or {}).items()}
        self.labels = dict(labels or {})
        self.rate_series_calls: list[tuple[UUID, datetime, datetime]] = []
        self.resolve_calls: list[tuple[UUID | None, UUID | None]] = []
        self.label_calls: list[UUID | None] = []

    def add_currency(self, currency_id: UUID, code: str, in_currency=None) -> None:
        """Register a currency entity: its code and what its own series is quoted in."""
        self.labels[currency_id] = code
        self.denominations[currency_id] = in_currency

    def set_rates(self, rate_entity_id: UUID, rates: dict[date, Decimal]) -> None:
        self.rates[rate_entity_id] = dict(rates)

    def resolve(self, from_id: UUID | None, to_id: UUID | None):
        self.resolve_calls.append((from_id, to_id))
        return resolve_path(from_id, to_id, self.denominations.get)

    def currency_code(self, currency_id: UUID | None) -> str:
        # A label is a database read in the real service, so the count matters.
        self.label_calls.append(currency_id)
        if currency_id is None:
            return PHP
        if currency_id not in self.labels:
            raise ValueError(f"currency entity {currency_id} not found")
        return self.labels[currency_id]

    def rate_series(
        self, rate_entity_id: UUID, start: datetime, end: datetime
    ) -> dict[date, Decimal]:
        self.rate_series_calls.append((rate_entity_id, start, end))
        return dict(self.rates.get(rate_entity_id, {}))


@pytest.fixture
def fx() -> FakeCurrencyConversion:
    return FakeCurrencyConversion()
