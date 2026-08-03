from __future__ import annotations

from uuid import UUID

from libram_database.db import Database
from libram_types.libram_types import PortfolioRecord
from portfolio_management import CreatePortfolioRequest, PortfolioNotFound, UpdatePortfolioRequest


class PortfolioService:
    """Portfolio CRUD operations."""

    def __init__(self, db: Database):
        self.db = db

    def _format_portfolio(self, record: PortfolioRecord) -> dict:
        return {
            "id": str(record.id),
            "name": record.name,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        }

    def create_portfolio(self, body: CreatePortfolioRequest) -> dict:
        record = self.db.create_portfolio(body.name)
        return self._format_portfolio(record)

    def list_portfolios(self) -> list[dict]:
        return [self._format_portfolio(r) for r in self.db.list_portfolios()]

    def update_portfolio(self, portfolio_id: UUID, body: UpdatePortfolioRequest) -> dict:
        record = self.db.update_portfolio(portfolio_id, body.name)
        if not record:
            raise PortfolioNotFound(f"portfolio not found: {portfolio_id}")
        return self._format_portfolio(record)

    def delete_portfolio(self, portfolio_id: UUID) -> None:
        deleted = self.db.delete_portfolio(portfolio_id)
        if not deleted:
            raise PortfolioNotFound(f"portfolio not found: {portfolio_id}")
