"""Pure dividend eligibility, conversion, and aggregation calculations.

The functions in this module deliberately know nothing about the database or
HTTP response models.  ``fx_lookup`` receives a currency entity id and the
date on which the conversion is valued, and returns the PHP exchange rate.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Callable, Iterable, Mapping, Optional

from libram_types.libram_types import (
    DividendEventRecord,
    PortfolioDividendRecord,
    PortfolioOrderRecord,
)
from portfolio_management import PortfolioValidationError

FxLookup = Callable[[object, date], object]


def _as_date(value: object) -> Optional[date]:
    if value is None:
        return None
    return value.date() if isinstance(value, datetime) else value  # type: ignore[return-value]


def _decimal(value: object) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value or "0"))


def _fee_value(fee: Optional[PortfolioDividendRecord]) -> tuple[Decimal, object]:
    if fee is None:
        return Decimal("0"), None
    return _decimal(fee.fees), fee.fees_entity_id


def _to_php(amount: Decimal, currency_id: object, conversion_date: date,
            fx_lookup: FxLookup) -> Decimal:
    if currency_id is None:
        return amount
    rate = fx_lookup(currency_id, conversion_date)
    if rate is None:
        raise PortfolioValidationError(
            f"no exchange rate found for currency entity {currency_id} "
            f"at or before {conversion_date.isoformat()}"
        )
    return amount * _decimal(rate)


def calculate_dividend(
    orders: Iterable[PortfolioOrderRecord],
    event: DividendEventRecord,
    fee: Optional[PortfolioDividendRecord] = None,
    fx_lookup: Optional[FxLookup] = None,
) -> tuple[Decimal, Decimal]:
    """Return ``(gross_gain_php, dividend_fees_php)`` for one portfolio/event.

    Only orders for the event's entity and strictly before its ex-date are
    replayed.  Sorting by date and id makes same-day transactions stable.
    """
    if event.ex_date is None:
        return Decimal("0"), Decimal("0")
    ex_date = _as_date(event.ex_date)
    assert ex_date is not None
    eligible_orders = []
    for order in orders:
        order_date = _as_date(order.date)
        if order.entity_id == event.entity_id and order_date is not None and order_date < ex_date:
            eligible_orders.append(order)
    eligible_orders.sort(key=lambda order: (_as_date(order.date) or date.min, str(order.id)))

    shares = Decimal("0")
    for order in eligible_orders:
        quantity = _decimal(order.shares)
        shares += quantity if order.type == "buy" else -quantity

    conversion_date = _as_date(event.payment_date) or ex_date
    amount = shares * _decimal(event.amount_per_share)
    fees, fee_currency_id = _fee_value(fee)
    if fx_lookup is None:
        fx_lookup = lambda _currency_id, _date: None
    gain_php = _to_php(amount, event.amount_per_share_entity_id,
                       conversion_date, fx_lookup)
    fees_php = _to_php(fees, fee_currency_id, conversion_date, fx_lookup)
    return gain_php, fees_php


def calculate_dividend_totals(
    orders: Iterable[PortfolioOrderRecord],
    events: Iterable[DividendEventRecord],
    fees_by_event: Mapping[object, PortfolioDividendRecord] | None = None,
    fx_lookup: Optional[FxLookup] = None,
) -> tuple[Decimal, Decimal]:
    """Accumulate gross gains and fees for one portfolio independently."""
    fees_by_event = fees_by_event or {}
    gain = Decimal("0")
    fees = Decimal("0")
    for event in events:
        event_gain, event_fees = calculate_dividend(
            orders, event, fees_by_event.get(event.id), fx_lookup
        )
        gain += event_gain
        fees += event_fees
    return gain, fees


def calculate_all_portfolios_dividend_totals(
    orders_by_portfolio: Mapping[object, Iterable[PortfolioOrderRecord]],
    events: Iterable[DividendEventRecord],
    fees_by_portfolio: Mapping[object, Mapping[object, PortfolioDividendRecord]] | None = None,
    fx_lookup: Optional[FxLookup] = None,
) -> tuple[Decimal, Decimal]:
    """Sum per-portfolio calculations without combining holdings.

    This matters when two portfolios hold the same security: replaying their
    orders as one stream would incorrectly allow one portfolio's buys to
    satisfy another portfolio's dividend eligibility.
    """
    event_list = list(events)
    fees_by_portfolio = fees_by_portfolio or {}
    gain = Decimal("0")
    fees = Decimal("0")
    for portfolio_id, portfolio_orders in orders_by_portfolio.items():
        portfolio_gain, portfolio_fees = calculate_dividend_totals(
            portfolio_orders,
            event_list,
            fees_by_portfolio.get(portfolio_id, {}),
            fx_lookup,
        )
        gain += portfolio_gain
        fees += portfolio_fees
    return gain, fees