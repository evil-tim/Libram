# ruff: noqa: DTZ001
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from libram_types.libram_types import (
    EntityRecord,
    PortfolioOrderRecord,
    PortfolioRecord,
)
from portfolio_management import (
    CreateOrderRequest,
    InsufficientShares,
    PortfolioNotFound,
    PortfolioValidationError,
)
from portfolio_management.order import OrderService
from portfolio_management.portfolio import PortfolioService


class PortfolioDb:
    def __init__(self, portfolio=None, entity=None):
        self.portfolio = portfolio
        self.entity = entity
        self.created = None

    def get_portfolio(self, portfolio_id):
        return (
            self.portfolio
            if self.portfolio and self.portfolio.id == portfolio_id
            else None
        )

    def create_portfolio(self, name, description=""):
        self.portfolio = PortfolioRecord(
            self.portfolio.id if self.portfolio else uuid4(),
            name,
            description=description,
        ) if self.portfolio else PortfolioRecord(uuid4(), name, description=description)
        return self.portfolio

    def update_portfolio(self, portfolio_id, name, description=""):
        if self.portfolio and portfolio_id == self.portfolio.id:
            self.portfolio.name = name
            self.portfolio.description = description
            return self.portfolio
        return None

    def delete_portfolio(self, portfolio_id):
        return bool(self.portfolio and portfolio_id == self.portfolio.id)

    def get_entity_by_id_raw(self, entity_id):
        return (
            {"code": "ABC", "name": "Alpha"}
            if self.entity and entity_id == self.entity.id
            else None
        )

    def create_order(self, **kwargs):
        self.created = kwargs
        return PortfolioOrderRecord(
            uuid4(),
            kwargs["portfolio_id"],
            kwargs["entity_id"],
            kwargs["date"],
            Decimal(str(kwargs["shares"])),
            kwargs["type"],
            Decimal(str(kwargs["cost_basis"])),
            fees=Decimal(str(kwargs["fees"])),
        )


class PriceManager:
    def __init__(self, entity=None):
        self.entity = entity

    def query_entities(self, *args):
        code = args[1] if len(args) > 1 else None
        return (
            [self.entity]
            if self.entity and (code is None or self.entity.code == code)
            else []
        )


def test_portfolio_service_formats_and_raises_not_found():
    pid = uuid4()
    record = PortfolioRecord(pid, "Growth", datetime(2026, 1, 1), None, "Core holdings")
    service = PortfolioService(PortfolioDb(record))
    assert service._format_portfolio(record)["id"] == str(pid)
    assert service._format_portfolio(record)["description"] == "Core holdings"
    with pytest.raises(PortfolioNotFound):
        service.update_portfolio(uuid4(), type("Body", (), {"name": "x", "description": "y"})())


def test_order_service_rejects_unknown_entity_and_invalid_date():
    pid = uuid4()
    db = PortfolioDb(PortfolioRecord(pid, "Growth"), EntityRecord(uuid4(), "ABC"))
    service = OrderService(PriceManager(db.entity), db)
    body = CreateOrderRequest(
        entity_code="NOPE", date="2026-01-01", shares=1, type="buy", cost_basis=10
    )
    with pytest.raises(PortfolioValidationError, match="entity not found"):
        service.create_order(pid, body)
    body = CreateOrderRequest(
        entity_code="ABC", date="bad", shares=1, type="buy", cost_basis=10
    )
    with pytest.raises(PortfolioValidationError, match="invalid date"):
        service.create_order(pid, body)


def test_sell_validation_rejects_more_than_held():
    service = OrderService(PriceManager(), PortfolioDb())
    entity = uuid4()
    buy = PortfolioOrderRecord(
        uuid4(), uuid4(), entity, datetime(2026, 1, 1), Decimal(2), "buy", Decimal(10)
    )
    with pytest.raises(InsufficientShares):
        service._validate_sell_insertion([buy], datetime(2026, 1, 2), 3)


def test_sell_validation_ignores_future_orders():
    service = OrderService(PriceManager(), PortfolioDb())
    entity = uuid4()
    future_buy = PortfolioOrderRecord(
        uuid4(), uuid4(), entity, datetime(2027, 1, 1), Decimal(10), "buy", Decimal(10)
    )
    with pytest.raises(InsufficientShares):
        service._validate_sell_insertion([future_buy], datetime(2026, 1, 2), 1)
