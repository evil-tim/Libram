# ruff: noqa: DTZ001
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from libram_types.libram_types import PortfolioOrderRecord
from portfolio_management.totals import TotalsService


def order(entity, when, shares, kind, cost, fees=0):
    return PortfolioOrderRecord(
        uuid4(),
        uuid4(),
        entity,
        when,
        Decimal(shares),
        kind,
        Decimal(cost),
        fees=Decimal(fees),
    )


def test_compute_position_uses_weighted_average_and_realized_gain():
    service = TotalsService(object())
    entity = uuid4()
    result = service._compute_position(
        [
            order(entity, datetime(2026, 1, 1), 10, "buy", 10, 2),
            order(entity, datetime(2026, 1, 2), 10, "buy", 20),
            order(entity, datetime(2026, 1, 3), 5, "sell", 30),
        ]
    )
    assert result["held_shares"] == Decimal(15)
    assert result["avg_cost"] == Decimal(15)
    assert result["total_cost_basis"] == Decimal(225)
    assert result["total_fees"] == Decimal(2)
    assert result["realized_gain"] == Decimal(75)


def test_compute_position_resets_average_cost_when_fully_sold():
    service = TotalsService(object())
    entity = uuid4()
    result = service._compute_position(
        [
            order(entity, datetime(2026, 1, 1), 2, "buy", 10),
            order(entity, datetime(2026, 1, 2), 2, "sell", 12),
        ]
    )
    assert result["held_shares"] == Decimal(0)
    assert result["avg_cost"] == Decimal(0)
    assert result["realized_gain"] == Decimal(4)
