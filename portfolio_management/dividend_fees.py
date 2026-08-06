from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from libram_database.db import Database
from price_management.client import PriceManagerClient
from portfolio_management import (
    DividendNotFound,
    PortfolioDividendCreateRequest,
    PortfolioDividendNotFound,
    PortfolioDividendUpdateRequest,
    PortfolioNotFound,
    PortfolioValidationError,
)


class DividendFeeService:
    """CRUD for fees attached to a portfolio/dividend-event pair."""

    def __init__(self, price_manager: PriceManagerClient, db: Database):
        self.price_manager = price_manager
        self.db = db

    def _resolve_currency_id(self, code: str | None) -> UUID | None:
        if code is None:
            return None  # PHP is represented by a NULL currency entity.
        entities = list(self.price_manager.query_entities(None, code, None, None))
        if not entities:
            raise PortfolioValidationError(f"entity not found: {code}")
        return entities[0].id

    def _require_scope(self, portfolio_id: UUID, event_id: UUID) -> None:
        if not self.db.get_portfolio(portfolio_id):
            raise PortfolioNotFound(f"portfolio not found: {portfolio_id}")
        if not self.db.get_dividend_event(event_id):
            raise DividendNotFound(f"dividend event not found: {event_id}")

    def _format(self, record) -> dict[str, Any]:
        currency = (
            self.db.get_entity_by_id_raw(record.fees_entity_id)
            if record.fees_entity_id
            else None
        )

        def _iso(d):
            return d.isoformat() if d is not None else None

        def _num(v):
            return float(v) if v is not None else None

        return {
            "id": str(record.id),
            "portfolio_id": str(record.portfolio_id),
            "dividend_event_id": str(record.dividend_event_id),
            "fees": _num(record.fees),
            "fees_entity_id": str(record.fees_entity_id) if record.fees_entity_id else None,
            "fees_entity_code": currency.get("code") if currency else None,
            "created_at": _iso(record.created_at),
            "updated_at": _iso(record.updated_at),
        }

    def get(self, portfolio_id: UUID, event_id: UUID) -> dict[str, Any]:
        self._require_scope(portfolio_id, event_id)
        record = self.db.get_portfolio_dividend(portfolio_id, event_id)
        if not record:
            raise PortfolioDividendNotFound(
                f"portfolio dividend association not found: {portfolio_id}/{event_id}"
            )
        return self._format(record)

    def create(
        self,
        portfolio_id: UUID,
        event_id: UUID,
        body: PortfolioDividendCreateRequest,
    ) -> dict[str, Any]:
        self._require_scope(portfolio_id, event_id)
        if self.db.get_portfolio_dividend(portfolio_id, event_id):
            raise PortfolioValidationError(
                f"portfolio dividend association already exists: {portfolio_id}/{event_id}"
            )
        try:
            record = self.db.create_portfolio_dividend(
                portfolio_id=portfolio_id,
                dividend_event_id=event_id,
                fees=body.fees,
                fees_entity_id=self._resolve_currency_id(body.fees_entity_code),
            )
        except IntegrityError as exc:
            raise PortfolioValidationError(
                f"portfolio dividend association already exists: {portfolio_id}/{event_id}"
            ) from exc
        return self._format(record)

    def update(
        self,
        portfolio_id: UUID,
        event_id: UUID,
        body: PortfolioDividendUpdateRequest,
    ) -> dict[str, Any]:
        self._require_scope(portfolio_id, event_id)
        if not self.db.get_portfolio_dividend(portfolio_id, event_id):
            raise PortfolioDividendNotFound(
                f"portfolio dividend association not found: {portfolio_id}/{event_id}"
            )
        values = body.model_dump(exclude_unset=True)
        if "fees_entity_code" in body.model_fields_set:
            values["fees_entity_id"] = self._resolve_currency_id(
                values.pop("fees_entity_code")
            )
        record = self.db.update_portfolio_dividend(portfolio_id, event_id, **values)
        if not record:
            raise PortfolioDividendNotFound(
                f"portfolio dividend association not found: {portfolio_id}/{event_id}"
            )
        return self._format(record)

    def delete(self, portfolio_id: UUID, event_id: UUID) -> None:
        self._require_scope(portfolio_id, event_id)
        if not self.db.delete_portfolio_dividend(portfolio_id, event_id):
            raise PortfolioDividendNotFound(
                f"portfolio dividend association not found: {portfolio_id}/{event_id}"
            )

    def list(self, portfolio_id: UUID) -> list[dict[str, Any]]:
        if not self.db.get_portfolio(portfolio_id):
            raise PortfolioNotFound(f"portfolio not found: {portfolio_id}")
        return [
            self._format(record)
            for record in self.db.list_portfolio_dividends_for_portfolio(portfolio_id)
        ]
