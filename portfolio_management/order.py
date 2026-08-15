from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from libram_database.db import Database
from libram_types.libram_types import PortfolioOrderRecord
from price_management.service import PriceManagerService
from portfolio_management import (
    CreateOrderRequest,
    InsufficientShares,
    OrderNotFound,
    PortfolioNotFound,
    PortfolioValidationError,
    UpdateOrderRequest,
)


class OrderService:
    """Order CRUD and validation operations."""

    def __init__(self, price_manager: PriceManagerService, db: Database):
        self.price_manager = price_manager
        self.db = db

    def _resolve_entity(self, entity_code: str):
        """Resolve an entity code to an EntityRecord. Raises PortfolioValidationError."""
        entities = list(self.price_manager.query_entities(None, entity_code, None, None))
        if not entities:
            raise PortfolioValidationError(f"entity not found: {entity_code}")
        return entities[0]

    def _resolve_entity_id(self, entity_code: str) -> UUID:
        return self._resolve_entity(entity_code).id

    def _entity_lookup(self, entity_ids: set[UUID]) -> dict[UUID, dict]:
        """Batch-lookup raw entity rows for a set of ids. Returns {id: {code, name}}."""
        out: dict[UUID, dict] = {}
        for eid in entity_ids:
            raw = self.db.get_entity_by_id_raw(eid)
            if raw:
                out[eid] = {"code": raw.get("code"), "name": raw.get("name")}
            else:
                out[eid] = {"code": None, "name": None}
        return out

    @staticmethod
    def _parse_iso(value: str, field_name: str) -> datetime:
        try:
            return datetime.fromisoformat(value)
        except (ValueError, TypeError) as exc:
            raise PortfolioValidationError(
                f"invalid {field_name}: '{value}'. must be a valid ISO 8601 datetime"
            ) from exc

    def _format_order(self, record: PortfolioOrderRecord, entity_map: dict[UUID, dict]) -> dict:
        entity = entity_map.get(record.entity_id, {})
        return {
            "id": str(record.id),
            "portfolio_id": str(record.portfolio_id),
            "entity_id": str(record.entity_id),
            "entity_code": entity.get("code"),
            "entity_name": entity.get("name"),
            "date": record.date.isoformat() if record.date else None,
            "shares": float(record.shares) if record.shares is not None else None,
            "type": record.type,
            "cost_basis": float(record.cost_basis) if record.cost_basis is not None else None,
            "cost_basis_entity_id": str(record.cost_basis_entity_id) if record.cost_basis_entity_id else None,
            "fees": float(record.fees) if record.fees is not None else None,
            "fees_entity_id": str(record.fees_entity_id) if record.fees_entity_id else None,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        }

    def create_order(self, portfolio_id: UUID, body: CreateOrderRequest) -> dict:
        if not self.db.get_portfolio(portfolio_id):
            raise PortfolioNotFound(f"portfolio not found: {portfolio_id}")

        entity = self._resolve_entity(body.entity_code)

        cost_basis_entity_id = None
        if body.cost_basis_entity_code:
            cost_basis_entity_id = self._resolve_entity_id(body.cost_basis_entity_code)
        fees_entity_id = None
        if body.fees_entity_code:
            fees_entity_id = self._resolve_entity_id(body.fees_entity_code)

        order_date = self._parse_iso(body.date, "date")

        existing = self.db.get_orders_for_entity(portfolio_id, entity.id)
        if body.type == "sell":
            self._validate_sell_insertion(existing, order_date, body.shares)

        record = self.db.create_order(
            portfolio_id=portfolio_id,
            entity_id=entity.id,
            date=order_date,
            shares=body.shares,
            type=body.type,
            cost_basis=body.cost_basis,
            cost_basis_entity_id=cost_basis_entity_id,
            fees=body.fees,
            fees_entity_id=fees_entity_id,
        )
        entity_map = self._entity_lookup({record.entity_id})
        return self._format_order(record, entity_map)

    def update_order(self, portfolio_id: UUID, order_id: UUID, body: UpdateOrderRequest) -> dict:
        existing = self.db.get_order_for_portfolio(order_id, portfolio_id)
        if not existing:
            raise OrderNotFound(f"order not found: {order_id} in portfolio {portfolio_id}")

        updates: dict = {}
        if body.entity_code is not None:
            entity = self._resolve_entity(body.entity_code)
            updates["entity_id"] = entity.id
        if body.date is not None:
            updates["date"] = self._parse_iso(body.date, "date")
        if body.shares is not None:
            updates["shares"] = body.shares
        if body.type is not None:
            updates["type"] = body.type
        if body.cost_basis is not None:
            updates["cost_basis"] = body.cost_basis
        if body.cost_basis_entity_code is not None:
            updates["cost_basis_entity_id"] = self._resolve_entity_id(body.cost_basis_entity_code)
        if body.fees is not None:
            updates["fees"] = body.fees
        if body.fees_entity_code is not None:
            updates["fees_entity_id"] = self._resolve_entity_id(body.fees_entity_code)

        revalidate_keys = {"entity_id", "date", "shares", "type"}
        if revalidate_keys & updates.keys():
            self._revalidate_sequence(portfolio_id, order_id, existing, updates)

        updated = self.db.update_order(order_id, **updates)
        if not updated:
            raise OrderNotFound(f"order not found: {order_id}")
        entity_map = self._entity_lookup({updated.entity_id})
        return self._format_order(updated, entity_map)

    def delete_order(self, order_id: UUID) -> None:
        deleted = self.db.delete_order(order_id)
        if not deleted:
            raise OrderNotFound(f"order not found: {order_id}")

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
        if not self.db.get_portfolio(portfolio_id):
            raise PortfolioNotFound(f"portfolio not found: {portfolio_id}")

        entity_id = None
        if entity_code:
            entity_id = self._resolve_entity(entity_code).id

        parsed_from = self._parse_iso(date_from, "date_from") if date_from else None
        parsed_to = self._parse_iso(date_to, "date_to") if date_to else None

        valid_sort_by = {"date", "entity_code", "shares", "cost_basis"}
        if sort_by not in valid_sort_by:
            raise PortfolioValidationError(
                f"invalid sort_by: '{sort_by}'. must be one of: {sorted(valid_sort_by)}"
            )
        valid_sort_order = {"asc", "desc"}
        if sort_order not in valid_sort_order:
            raise PortfolioValidationError(
                f"invalid sort_order: '{sort_order}'. must be one of: {sorted(valid_sort_order)}"
            )

        orders = self.db.query_orders(
            portfolio_id=portfolio_id,
            page=page,
            size=size,
            entity_id=entity_id,
            order_type=order_type,
            date_from=parsed_from,
            date_to=parsed_to,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        total = self.db.count_orders(
            portfolio_id=portfolio_id,
            entity_id=entity_id,
            order_type=order_type,
            date_from=parsed_from,
            date_to=parsed_to,
        )

        entity_map = self._entity_lookup({o.entity_id for o in orders})
        return {
            "items": [self._format_order(o, entity_map) for o in orders],
            "total": total,
            "page": page,
            "size": size,
        }

    def _validate_sell_insertion(
        self,
        existing_orders: list[PortfolioOrderRecord],
        new_date: datetime,
        new_shares: float,
    ) -> None:
        held = Decimal(0)
        for o in existing_orders:
            if o.date and o.date > new_date:
                continue
            shares = o.shares or Decimal(0)
            if o.type == "buy":
                held += shares
            else:
                held -= shares
        if held < Decimal(str(new_shares)):
            raise InsufficientShares(
                f"insufficient shares held at {new_date.isoformat()}: "
                f"held={float(held)}, requested sell={new_shares}"
            )

    def _revalidate_sequence(
        self,
        portfolio_id: UUID,
        order_id: UUID,
        current: PortfolioOrderRecord,
        updates: dict,
    ) -> None:
        entity_id = updates.get("entity_id", current.entity_id)

        peer_orders = [
            o for o in self.db.get_orders_for_entity(portfolio_id, entity_id)
            if o.id != order_id
        ]

        merged = PortfolioOrderRecord(
            id=current.id,
            portfolio_id=current.portfolio_id,
            entity_id=entity_id,
            date=updates.get("date", current.date),
            shares=updates.get("shares", current.shares),
            type=updates.get("type", current.type),
            cost_basis=current.cost_basis,
            cost_basis_entity_id=current.cost_basis_entity_id,
            fees=current.fees,
            fees_entity_id=current.fees_entity_id,
            created_at=current.created_at,
            updated_at=current.updated_at,
        )
        sequence = peer_orders + [merged]
        sequence.sort(key=lambda o: o.date or datetime.min.replace(tzinfo=timezone.utc))

        held = Decimal(0)
        for o in sequence:
            shares = o.shares or Decimal(0)
            if o.type == "buy":
                held += shares
            else:
                if held < shares:
                    raise InsufficientShares(
                        f"updating this order would result in insufficient shares "
                        f"for entity at {o.date.isoformat() if o.date else 'unknown'}: "
                        f"held={float(held)}, sell={float(shares)}"
                    )
                held -= shares
