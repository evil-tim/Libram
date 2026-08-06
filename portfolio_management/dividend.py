from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from libram_database.db import Database
from price_management.client import PriceManagerClient
from portfolio_management import (
    DividendEventCreateRequest,
    DividendEventUpdateRequest,
    DividendNotFound,
    PortfolioValidationError,
)


class DividendService:
    """CRUD operations for issuer-level dividend events."""

    def __init__(self, price_manager: PriceManagerClient, db: Database):
        self.price_manager = price_manager
        self.db = db

    def _resolve_entity(self, code: str):
        entities = list(self.price_manager.query_entities(None, code, None, None))
        if not entities:
            raise PortfolioValidationError(f"entity not found: {code}")
        return entities[0]

    def _format(self, record) -> dict[str, Any]:
        entity = self.db.get_entity_by_id_raw(record.entity_id) or {}
        currency = (
            self.db.get_entity_by_id_raw(record.amount_per_share_entity_id)
            if record.amount_per_share_entity_id
            else None
        )

        def _iso(d):
            return d.isoformat() if d is not None else None

        def _num(v):
            return float(v) if v is not None else None

        return {
            "id": str(record.id),
            "entity_id": str(record.entity_id),
            "entity_code": entity.get("code"),
            "entity_name": entity.get("name"),
            "declaration_date": _iso(record.declaration_date),
            "ex_date": _iso(record.ex_date),
            "record_date": _iso(record.record_date),
            "payment_date": _iso(record.payment_date),
            "dividend_type": record.dividend_type,
            "amount_per_share": _num(record.amount_per_share),
            "amount_per_share_entity_id": str(record.amount_per_share_entity_id) if record.amount_per_share_entity_id else None,
            "amount_per_share_entity_code": currency.get("code") if currency else None,
            "created_at": _iso(record.created_at),
            "updated_at": _iso(record.updated_at),
        }

    def create(self, body: DividendEventCreateRequest) -> dict[str, Any]:
        entity = self._resolve_entity(body.entity_code)
        currency = (
            self._resolve_entity(body.amount_per_share_entity_code)
            if body.amount_per_share_entity_code
            else None
        )
        record = self.db.create_dividend_event(
            entity_id=entity.id,
            declaration_date=body.declaration_date,
            ex_date=body.ex_date,
            record_date=body.record_date,
            payment_date=body.payment_date,
            dividend_type=body.dividend_type,
            amount_per_share=body.amount_per_share,
            amount_per_share_entity_id=currency.id if currency else None,
        )
        return self._format(record)

    def list(
        self,
        entity_code: str | None = None,
        ex_date_from: date | None = None,
        ex_date_to: date | None = None,
    ) -> list[dict[str, Any]]:
        entity_id = self._resolve_entity(entity_code).id if entity_code else None
        return [
            self._format(record)
            for record in self.db.list_dividend_events(entity_id, ex_date_from, ex_date_to)
        ]

    def get(self, event_id: UUID) -> dict[str, Any]:
        record = self.db.get_dividend_event(event_id)
        if not record:
            raise DividendNotFound(f"dividend event not found: {event_id}")
        return self._format(record)

    def update(
        self, event_id: UUID, body: DividendEventUpdateRequest
    ) -> dict[str, Any]:
        if not self.db.get_dividend_event(event_id):
            raise DividendNotFound(f"dividend event not found: {event_id}")

        values = body.model_dump(exclude_unset=True)
        values.pop("entity_code", None)
        if body.entity_code is not None:
            values["entity_id"] = self._resolve_entity(body.entity_code).id

        if "amount_per_share_entity_code" in body.model_fields_set:
            code = values.pop("amount_per_share_entity_code")
            values["amount_per_share_entity_id"] = (
                self._resolve_entity(code).id if code is not None else None
            )

        record = self.db.update_dividend_event(event_id, **values)
        if not record:
            raise DividendNotFound(f"dividend event not found: {event_id}")
        return self._format(record)

    def delete(self, event_id: UUID) -> None:
        if not self.db.delete_dividend_event(event_id):
            raise DividendNotFound(f"dividend event not found: {event_id}")
