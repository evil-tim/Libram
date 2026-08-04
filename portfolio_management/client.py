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


class PortfolioManagerClient:
    """High-level client for portfolio and order management."""

    def __init__(self, price_manager: PriceManagerClient, db: Database):
        self.price_manager = price_manager
        self.db = db
        self.portfolio_service = PortfolioService(db)
        self.order_service = OrderService(price_manager, db)
        self.totals_service = TotalsService(db)

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
