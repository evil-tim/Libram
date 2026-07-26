from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from libram_database.db import Database
from libram_types.libram_types import PortfolioOrderRecord, PortfolioRecord
from price_management.client import PriceManagerClient

from portfolio_management import (
    CreateOrderRequest,
    CreatePortfolioRequest,
    InsufficientShares,
    OrderNotFound,
    PortfolioNotFound,
    PortfolioValidationError,
    UpdateOrderRequest,
    UpdatePortfolioRequest,
)


class PortfolioManagerClient:
    """High-level client for portfolio and order management, including
    position totals computed via the average-cost method.
    """

    def __init__(self, price_manager: PriceManagerClient, db: Database):
        self.price_manager = price_manager
        self.db = db

    # ------------------------------------------------------------------
    # entity resolution helpers
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # formatting helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_iso(value: str, field_name: str) -> datetime:
        try:
            return datetime.fromisoformat(value)
        except (ValueError, TypeError) as exc:
            raise PortfolioValidationError(
                f"invalid {field_name}: '{value}'. must be a valid ISO 8601 datetime"
            ) from exc

    def _format_portfolio(self, record: PortfolioRecord) -> dict:
        return {
            "id": str(record.id),
            "name": record.name,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        }

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

    # ------------------------------------------------------------------
    # portfolio CRUD
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # order CRUD
    # ------------------------------------------------------------------
    def create_order(self, portfolio_id: UUID, body: CreateOrderRequest) -> dict:
        # portfolio must exist
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

        # Validate sell sufficiency against existing orders for this entity.
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

        # Build the update payload from the request, resolving entity codes.
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

        # Re-validate sell sufficiency if shares/type/date/entity changed in a
        # way that affects the chronological position.
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

    # ------------------------------------------------------------------
    # sell-sufficiency validation
    # ------------------------------------------------------------------
    def _validate_sell_insertion(
        self,
        existing_orders: list[PortfolioOrderRecord],
        new_date: datetime,
        new_shares: float,
    ) -> None:
        """Check that a new sell order at `new_date` for `new_shares` is covered
        by buys on or before that date. Existing orders after `new_date` are
        not considered (they may themselves become invalid, which is out of
        scope for this check).
        """
        held = Decimal(0)
        for o in existing_orders:
            if o.date and o.date > new_date:
                continue
            shares = o.shares or Decimal(0)
            if o.type == "buy":
                held += shares
            else:  # sell
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
        """Re-run the full chronological simulation for the entity, replacing
        the order being updated with its post-update values. Raises
        InsufficientShares if any sell in the sequence exceeds holdings.
        """
        # Determine the entity to validate against: the updated entity if
        # entity_id is changing, otherwise the current one.
        entity_id = updates.get("entity_id", current.entity_id)

        peer_orders = [
            o for o in self.db.get_orders_for_entity(portfolio_id, entity_id)
            if o.id != order_id
        ]

        # Construct the updated order snapshot.
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
            else:  # sell
                if held < shares:
                    raise InsufficientShares(
                        f"updating this order would result in insufficient shares "
                        f"for entity at {o.date.isoformat() if o.date else 'unknown'}: "
                        f"held={float(held)}, sell={float(shares)}"
                    )
                held -= shares

    # ------------------------------------------------------------------
    # totals computation (average cost method)
    # ------------------------------------------------------------------
    def _fx_to_php(self, amount: Decimal, currency_entity_id: Optional[UUID], at_date: datetime) -> Decimal:
        """Convert `amount` from the given currency to PHP using the price
        table rate at or before `at_date`. NULL currency means PHP (no-op).
        Raises PortfolioValidationError if no rate is found.
        """
        if currency_entity_id is None:
            return amount
        rate = self.db.get_price_at_or_before(currency_entity_id, at_date)
        if rate is None:
            raise PortfolioValidationError(
                f"no exchange rate found for currency entity {currency_entity_id} "
                f"at or before {at_date.isoformat()}"
            )
        if not isinstance(rate, Decimal):
            rate = Decimal(str(rate))
        return amount * rate

    def _compute_position(self, orders: list[PortfolioOrderRecord]) -> dict:
        """Simulate a single entity's position chronologically (average cost).

        Returns: held_shares, avg_cost, total_cost_basis, total_fees,
        realized_gain (all in PHP, as Decimal).
        """
        held = Decimal(0)
        avg_cost = Decimal(0)
        total_fees = Decimal(0)
        realized_gain = Decimal(0)

        for o in orders:
            shares = o.shares or Decimal(0)
            cost_basis = o.cost_basis or Decimal(0)
            fees = o.fees or Decimal(0)
            order_date = o.date or datetime.min.replace(tzinfo=timezone.utc)

            cost_php = self._fx_to_php(cost_basis, o.cost_basis_entity_id, order_date)
            fees_php = self._fx_to_php(fees, o.fees_entity_id, order_date)

            if o.type == "buy":
                new_total = held * avg_cost + shares * cost_php
                new_held = held + shares
                avg_cost = (new_total / new_held) if new_held > 0 else Decimal(0)
                held = new_held
                total_fees += fees_php
            else:  # sell
                realized_gain += (cost_php - avg_cost) * shares - fees_php
                held -= shares
                total_fees += fees_php
                if held <= 0:
                    held = Decimal(0)
                    avg_cost = Decimal(0)

        return {
            "held_shares": held,
            "avg_cost": avg_cost,
            "total_cost_basis": held * avg_cost,
            "total_fees": total_fees,
            "realized_gain": realized_gain,
        }

    def _load_orders_grouped(self, portfolio_id: Optional[UUID]) -> dict[UUID, list[PortfolioOrderRecord]]:
        """Load orders (date ASC) and group by entity_id."""
        if portfolio_id is not None:
            orders = self.db.get_all_orders(portfolio_id)
        else:
            orders = self.db.get_all_orders_across_portfolios()
        grouped: dict[UUID, list[PortfolioOrderRecord]] = {}
        for o in orders:
            grouped.setdefault(o.entity_id, []).append(o)
        return grouped

    def compute_totals(self, portfolio_id: Optional[UUID]) -> dict:
        grouped = self._load_orders_grouped(portfolio_id)

        total_cost_basis = Decimal(0)
        total_fees = Decimal(0)
        total_current_value = Decimal(0)
        total_unrealized_gain = Decimal(0)
        total_realized_gain = Decimal(0)

        for entity_id, orders in grouped.items():
            pos = self._compute_position(orders)
            latest = self.db.get_latest_price(entity_id)
            latest_price = Decimal(str(latest)) if latest is not None else Decimal(0)
            current_value = pos["held_shares"] * latest_price

            total_cost_basis += pos["total_cost_basis"]
            total_fees += pos["total_fees"]
            total_current_value += current_value
            total_unrealized_gain += current_value - pos["total_cost_basis"]
            total_realized_gain += pos["realized_gain"]

        response: dict = {
            "portfolio_id": str(portfolio_id) if portfolio_id else None,
            "total_cost_basis": float(total_cost_basis),
            "total_fees": float(total_fees),
            "total_current_value": float(total_current_value),
            "total_unrealized_gain": float(total_unrealized_gain),
            "total_realized_gain": float(total_realized_gain),
            "as_of": datetime.now(timezone.utc).isoformat(),
            "currency": "PHP",
        }
        if portfolio_id is not None:
            portfolio = self.db.get_portfolio(portfolio_id)
            response["portfolio_name"] = portfolio.name if portfolio else None
        return response

    def compute_totals_by_entity(self, portfolio_id: Optional[UUID]) -> dict:
        grouped = self._load_orders_grouped(portfolio_id)
        entity_map = self._entity_lookup(set(grouped.keys()))

        entities = []
        total_cost_basis = Decimal(0)
        total_fees = Decimal(0)
        total_current_value = Decimal(0)
        total_unrealized_gain = Decimal(0)
        total_realized_gain = Decimal(0)

        for entity_id, orders in grouped.items():
            pos = self._compute_position(orders)
            latest = self.db.get_latest_price(entity_id)
            latest_price = Decimal(str(latest)) if latest is not None else Decimal(0)
            current_value = pos["held_shares"] * latest_price
            unrealized = current_value - pos["total_cost_basis"]

            info = entity_map.get(entity_id, {})
            entities.append({
                "entity_id": str(entity_id),
                "entity_code": info.get("code"),
                "entity_name": info.get("name"),
                "shares_held": float(pos["held_shares"]),
                "avg_cost_basis": float(pos["avg_cost"]),
                "total_cost_basis": float(pos["total_cost_basis"]),
                "total_fees": float(pos["total_fees"]),
                "current_price": float(latest_price),
                "current_value": float(current_value),
                "unrealized_gain": float(unrealized),
                "realized_gain": float(pos["realized_gain"]),
            })

            total_cost_basis += pos["total_cost_basis"]
            total_fees += pos["total_fees"]
            total_current_value += current_value
            total_unrealized_gain += unrealized
            total_realized_gain += pos["realized_gain"]

        return {
            "portfolio_id": str(portfolio_id) if portfolio_id else None,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "currency": "PHP",
            "entities": entities,
            "totals": {
                "total_cost_basis": float(total_cost_basis),
                "total_fees": float(total_fees),
                "total_current_value": float(total_current_value),
                "total_unrealized_gain": float(total_unrealized_gain),
                "total_realized_gain": float(total_realized_gain),
            },
        }
