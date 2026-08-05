from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from libram_database.db import Database
from libram_types.libram_types import PortfolioOrderRecord
from portfolio_management import PortfolioValidationError


class TotalsService:
    """Portfolio position totals and average-cost computations."""

    def __init__(self, db: Database):
        self.db = db

    def _fx_to_php(self, amount: Decimal, currency_entity_id: Optional[UUID], at_date: datetime) -> Decimal:
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
            total_fees += fees_php

            if o.type == "buy":
                new_total = held * avg_cost + shares * cost_php
                new_held = held + shares
                avg_cost = (new_total / new_held) if new_held > 0 else Decimal(0)
                held = new_held
            else:
                realized_gain += (cost_php - avg_cost) * shares
                held -= shares
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
        if portfolio_id is not None:
            orders = self.db.get_all_orders(portfolio_id)
        else:
            orders = self.db.get_all_orders_across_portfolios()
        grouped: dict[UUID, list[PortfolioOrderRecord]] = {}
        for o in orders:
            grouped.setdefault(o.entity_id, []).append(o)
        return grouped

    def _entity_lookup(self, entity_ids: set[UUID]) -> dict[UUID, dict]:
        out: dict[UUID, dict] = {}
        for eid in entity_ids:
            raw = self.db.get_entity_by_id_raw(eid)
            if raw:
                out[eid] = {"code": raw.get("code"), "name": raw.get("name")}
            else:
                out[eid] = {"code": None, "name": None}
        return out

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
