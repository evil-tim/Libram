from __future__ import annotations

from typing import Optional
from uuid import UUID

from libram_database.db import Database
from price_management.client import PriceManagerClient

from portfolio_management import (
    CreateOrderRequest,
    CreatePortfolioRequest,
    UpdateOrderRequest,
    UpdatePortfolioRequest,
)
from portfolio_management.order import OrderService
from portfolio_management.portfolio import PortfolioService
from portfolio_management.totals import TotalsService
from portfolio_management.dividend import DividendService
from portfolio_management.dividend_fees import DividendFeeService


class PortfolioManagerClient:
    """High-level client for portfolio and order management."""

    def __init__(self, price_manager: PriceManagerClient, db: Database):
        self.price_manager = price_manager
        self.db = db
        self.portfolio_service = PortfolioService(db)
        self.order_service = OrderService(price_manager, db)
        self.totals_service = TotalsService(db)
        self.dividend_service = DividendService(price_manager, db)
        self.dividend_fee_service = DividendFeeService(price_manager, db)

    def create_dividend(self, body): return self.dividend_service.create(body)
    def list_dividends(self, entity_code=None, ex_date_from=None, ex_date_to=None): return self.dividend_service.list(entity_code, ex_date_from, ex_date_to)
    def get_dividend(self, dividend_id): return self.dividend_service.get(dividend_id)
    def update_dividend(self, dividend_id, body): return self.dividend_service.update(dividend_id, body)
    def delete_dividend(self, dividend_id): return self.dividend_service.delete(dividend_id)
    def create_dividend_fee(self, portfolio_id, event_id, body): return self.dividend_fee_service.create(portfolio_id, event_id, body)
    def get_dividend_fee(self, portfolio_id, event_id): return self.dividend_fee_service.get(portfolio_id, event_id)
    def update_dividend_fee(self, portfolio_id, event_id, body): return self.dividend_fee_service.update(portfolio_id, event_id, body)
    def delete_dividend_fee(self, portfolio_id, event_id): return self.dividend_fee_service.delete(portfolio_id, event_id)
    def list_dividend_fees(self, portfolio_id): return self.dividend_fee_service.list(portfolio_id)

    def compute_dividend_totals(self, portfolio_id: Optional[UUID] = None):
        return self.totals_service.compute_dividend_totals(portfolio_id)

    def create_portfolio(self, body: CreatePortfolioRequest) -> dict:
        return self.portfolio_service.create_portfolio(body)

    def list_portfolios(self) -> list[dict]:
        return self.portfolio_service.list_portfolios()

    def update_portfolio(self, portfolio_id: UUID, body: UpdatePortfolioRequest) -> dict:
        return self.portfolio_service.update_portfolio(portfolio_id, body)

    def delete_portfolio(self, portfolio_id: UUID) -> None:
        return self.portfolio_service.delete_portfolio(portfolio_id)

    def create_order(self, portfolio_id: UUID, body: CreateOrderRequest) -> dict:
        return self.order_service.create_order(portfolio_id, body)

    def update_order(self, portfolio_id: UUID, order_id: UUID, body: UpdateOrderRequest) -> dict:
        return self.order_service.update_order(portfolio_id, order_id, body)

    def delete_order(self, order_id: UUID) -> None:
        return self.order_service.delete_order(order_id)

    def list_orders(
        self,
        portfolio_id: UUID,
        page: int = 0,
        size: int = 20,
        entity_code: Optional[str] = None,
        order_type: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        sort_by: str = "date",
        sort_order: str = "desc",
    ) -> dict:
        return self.order_service.list_orders(
            portfolio_id=portfolio_id,
            page=page,
            size=size,
            entity_code=entity_code,
            order_type=order_type,
            date_from=date_from,
            date_to=date_to,
            sort_by=sort_by,
            sort_order=sort_order,
        )

    def compute_totals(self, portfolio_id: Optional[UUID]) -> dict:
        return self.totals_service.compute_totals(portfolio_id)

    def compute_totals_by_entity(self, portfolio_id: Optional[UUID]) -> dict:
        return self.totals_service.compute_totals_by_entity(portfolio_id)
