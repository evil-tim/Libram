# ruff: noqa: DTZ001
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from libram_types.libram_types import PortfolioOrderRecord
from portfolio_management.totals import TotalsService


def order(entity, when, shares, kind, cost, fees=0, portfolio_id=None):
    return PortfolioOrderRecord(
        uuid4(),
        portfolio_id or uuid4(),
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


def test_compute_totals_by_entity_includes_percentage_of_current_portfolio_value():
    portfolio_id = uuid4()
    first_entity, second_entity = uuid4(), uuid4()
    orders = [
        order(first_entity, datetime(2026, 1, 1), 2, "buy", 10, portfolio_id=portfolio_id),
        order(second_entity, datetime(2026, 1, 1), 1, "buy", 20, portfolio_id=portfolio_id),
    ]

    class FakeDatabase:
        def get_all_orders(self, requested_portfolio_id):
            assert requested_portfolio_id == portfolio_id
            return orders

        def get_entity_by_id_raw(self, entity_id):
            return {first_entity: {"code": "AAA", "name": "First"}, second_entity: {"code": "BBB", "name": "Second"}}[entity_id]

        def get_latest_price(self, entity_id):
            return {first_entity: Decimal("15"), second_entity: Decimal("30")}[entity_id]

        def list_portfolio_dividends_for_portfolio(self, requested_portfolio_id):
            assert requested_portfolio_id == portfolio_id
            return []

        def list_dividend_events(self):
            return []

        def get_portfolio(self, requested_portfolio_id):
            return SimpleNamespace(name="Test") if requested_portfolio_id == portfolio_id else None

    result = TotalsService(FakeDatabase()).compute_totals_by_entity(portfolio_id)

    assert [entity["current_value_percentage"] for entity in result["entities"]] == [50.0, 50.0]


def test_compute_totals_by_entity_returns_zero_percentage_for_empty_portfolio_value():
    portfolio_id = uuid4()
    entity = uuid4()

    class FakeDatabase:
        def get_all_orders(self, requested_portfolio_id):
            return [order(entity, datetime(2026, 1, 1), 1, "buy", 10, portfolio_id=portfolio_id)]

        def get_entity_by_id_raw(self, entity_id):
            return {"code": "AAA", "name": "First"}

        def get_latest_price(self, entity_id):
            return Decimal("0")

        def list_portfolio_dividends_for_portfolio(self, requested_portfolio_id):
            return []

        def list_dividend_events(self):
            return []

        def get_portfolio(self, requested_portfolio_id):
            return SimpleNamespace(name="Test")

    result = TotalsService(FakeDatabase()).compute_totals_by_entity(portfolio_id)

    assert result["entities"][0]["current_value_percentage"] == 0.0
    assert result["totals"]["total_current_value"] == 0.0
