from datetime import date
from decimal import Decimal
from uuid import uuid4

from libram_types.libram_types import (
    DividendEventRecord,
    PortfolioDividendRecord,
    PortfolioOrderRecord,
)
from portfolio_management.dividend_calculation import (
    calculate_all_portfolios_dividend_totals,
    calculate_dividend,
    calculate_dividend_totals,
)


def order(portfolio, entity, when, shares, kind="buy", cost="1"):
    return PortfolioOrderRecord(
        uuid4(), portfolio, entity, when, Decimal(shares), kind, Decimal(cost)
    )


def test_dividend_counts_only_holdings_before_ex_date_and_applies_fee():
    portfolio, entity = uuid4(), uuid4()
    event = DividendEventRecord(
        uuid4(),
        entity,
        date(2026, 8, 10),
        payment_date=date(2026, 8, 20),
        amount_per_share=Decimal(2),
    )
    fee = PortfolioDividendRecord(uuid4(), portfolio, event.id, Decimal(3))
    orders = [
        order(portfolio, entity, date(2026, 8, 1), "10"),
        order(portfolio, entity, date(2026, 8, 10), "5"),
    ]
    assert calculate_dividend(orders, event, fee) == (Decimal(20), Decimal(3))


def test_dividend_currency_conversion_and_missing_rate():
    portfolio, entity, usd = uuid4(), uuid4(), uuid4()
    event = DividendEventRecord(
        uuid4(),
        entity,
        date(2026, 8, 10),
        amount_per_share=Decimal(2),
        amount_per_share_entity_id=usd,
    )
    lookup = lambda currency, when: Decimal(56)
    assert calculate_dividend(
        [order(portfolio, entity, date(2026, 8, 1), "2")], event, fx_lookup=lookup
    )[0] == Decimal(224)


def test_dividend_totals_keep_portfolios_independent():
    entity = uuid4()
    p1, p2 = uuid4(), uuid4()
    event = DividendEventRecord(
        uuid4(), entity, date(2026, 8, 10), amount_per_share=Decimal(1)
    )
    totals = calculate_all_portfolios_dividend_totals(
        {
            p1: [order(p1, entity, date(2026, 8, 1), "2")],
            p2: [order(p2, entity, date(2026, 8, 1), "3")],
        },
        [event],
    )
    assert totals == (Decimal(5), Decimal(0))
    assert calculate_dividend_totals([], [event]) == (Decimal(0), Decimal(0))


def test_missing_ex_date_has_no_dividend():
    event = DividendEventRecord(uuid4(), uuid4(), None, amount_per_share=Decimal(2))
    assert calculate_dividend([], event) == (Decimal(0), Decimal(0))
