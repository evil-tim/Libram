from __future__ import annotations

from datetime import UTC, date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from libram_database.db import Database
from libram_types.libram_types import PortfolioDividendRecord, PortfolioOrderRecord
from portfolio_management import PortfolioValidationError
from portfolio_management.dividend_calculation import calculate_dividend_totals


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
        return amount * (rate if isinstance(rate, Decimal) else Decimal(str(rate)))

    def _compute_position(self, orders: list[PortfolioOrderRecord]) -> dict:
        held = Decimal(0)
        avg_cost = Decimal(0)
        total_fees = Decimal(0)
        realized_gain = Decimal(0)
        for order in sorted(orders, key=lambda o: (o.date or datetime.min.replace(tzinfo=timezone.utc), str(o.id))):
            shares = order.shares or Decimal(0)
            at_date = order.date or datetime.min.replace(tzinfo=UTC)
            cost = self._fx_to_php(order.cost_basis or Decimal(0), order.cost_basis_entity_id, at_date)
            total_fees += self._fx_to_php(order.fees or Decimal(0), order.fees_entity_id, at_date)
            if order.type == "buy":
                total = held * avg_cost + shares * cost
                held += shares
                avg_cost = total / held if held else Decimal(0)
            else:
                realized_gain += (cost - avg_cost) * shares
                held -= shares
                if held <= 0:
                    held = Decimal(0)
                    avg_cost = Decimal(0)
        return {"held_shares": held, "avg_cost": avg_cost, "total_cost_basis": held * avg_cost,
                "total_fees": total_fees, "realized_gain": realized_gain}

    def _load_orders_grouped(self, portfolio_id: Optional[UUID]) -> dict[UUID, list[PortfolioOrderRecord]]:
        orders = (self.db.get_all_orders(portfolio_id) if portfolio_id is not None
                  else self.db.get_all_orders_across_portfolios())
        grouped: dict[UUID, list[PortfolioOrderRecord]] = {}
        for order in orders:
            grouped.setdefault(order.entity_id, []).append(order)
        return grouped

    def _entity_lookup(self, entity_ids: set[UUID]) -> dict[UUID, dict]:
        result = {}
        for entity_id in entity_ids:
            raw = self.db.get_entity_by_id_raw(entity_id) or {}
            result[entity_id] = {"code": raw.get("code"), "name": raw.get("name")}
        return result

    def _fx_lookup(self, currency_id: UUID, at_date: date) -> Decimal | None:
        return self.db.get_price_at_or_before(
            currency_id, datetime.combine(at_date, datetime.min.time(), tzinfo=UTC)
        )

    def _dividends_for_portfolio(self, portfolio_id: UUID):
        fees = {record.dividend_event_id: record
                for record in self.db.list_portfolio_dividends_for_portfolio(portfolio_id)}
        events = self.db.list_dividend_events()
        paid_events = [e for e in events if e.payment_date is not None and e.payment_date <= datetime.now(timezone.utc).date()]
        return calculate_dividend_totals(
            self.db.get_all_orders(portfolio_id), paid_events, fees, self._fx_lookup
        )

    def _dividends(self, portfolio_id: Optional[UUID]):
        if portfolio_id is not None:
            return self._dividends_for_portfolio(portfolio_id)
        gain = Decimal(0)
        fees = Decimal(0)
        for portfolio in self.db.list_portfolios():
            portfolio_gain, portfolio_fees = self._dividends_for_portfolio(portfolio.id)
            gain += portfolio_gain
            fees += portfolio_fees
        return gain, fees

    def compute_dividend_totals(self, portfolio_id: Optional[UUID] = None) -> dict:
        gain, fees = self._dividends(portfolio_id)
        return {"portfolio_id": str(portfolio_id) if portfolio_id else None,
                "total_dividend_gain": float(gain), "total_dividend_fees": float(fees), "currency": "PHP"}

    def compute_totals(self, portfolio_id: Optional[UUID]) -> dict:
        grouped = self._load_orders_grouped(portfolio_id)
        cost = fees = value = realized = Decimal(0)
        for entity_id, orders in grouped.items():
            position = self._compute_position(orders)
            latest = Decimal(str(self.db.get_latest_price(entity_id) or 0))
            cost += position["total_cost_basis"]
            fees += position["total_fees"]
            value += position["held_shares"] * latest
            realized += position["realized_gain"]
        dividend_gain, dividend_fees = self._dividends(portfolio_id)
        response = {"portfolio_id": str(portfolio_id) if portfolio_id else None,
                    "total_cost_basis": float(cost), "total_fees": float(fees),
                    "total_current_value": float(value), "total_unrealized_gain": float(value - cost),
                    "total_realized_gain": float(realized), "total_dividend_gain": float(dividend_gain),
                    "total_dividend_fees": float(dividend_fees),
                    "as_of": datetime.now(UTC).isoformat(), "currency": "PHP"}
        if portfolio_id is not None:
            portfolio = self.db.get_portfolio(portfolio_id)
            response["portfolio_name"] = portfolio.name if portfolio else None
        return response

    def compute_totals_by_entity(self, portfolio_id: Optional[UUID]) -> dict:
        grouped = self._load_orders_grouped(portfolio_id)
        entity_map = self._entity_lookup(set(grouped))
        fee_maps: dict[UUID, dict[UUID, PortfolioDividendRecord]] = {}
        if portfolio_id is not None:
            fee_maps[portfolio_id] = {r.dividend_event_id: r for r in self.db.list_portfolio_dividends_for_portfolio(portfolio_id)}
        else:
            for portfolio in self.db.list_portfolios():
                fee_maps[portfolio.id] = {r.dividend_event_id: r for r in self.db.list_portfolio_dividends_for_portfolio(portfolio.id)}
        all_events = self.db.list_dividend_events()
        # filter to events that have already been distributed
        paid_events = [e for e in all_events if e.payment_date is not None and e.payment_date <= datetime.now(timezone.utc).date()]
        entities = []
        for entity_id, orders in grouped.items():
            position = self._compute_position(orders)
            latest = Decimal(str(self.db.get_latest_price(entity_id) or 0))
            gain = fees = Decimal(0)
            # filter events by entity_id
            events = [e for e in paid_events if e.entity_id == entity_id]
            if portfolio_id is not None:
                gain, fees = calculate_dividend_totals(orders, events, fee_maps[portfolio_id], self._fx_lookup)
            else:
                for current_id, fee_map in fee_maps.items():
                    current_orders = [o for o in self.db.get_all_orders(current_id) if o.entity_id == entity_id]
                    current_gain, current_fees = calculate_dividend_totals(current_orders, events, fee_map, self._fx_lookup)
                    gain += current_gain
                    fees += current_fees
            info = entity_map[entity_id]
            current_value = position["held_shares"] * latest
            entities.append({"entity_id": str(entity_id), "entity_code": info["code"], "entity_name": info["name"],
                             "shares_held": float(position["held_shares"]), "avg_cost_basis": float(position["avg_cost"]),
                             "total_cost_basis": float(position["total_cost_basis"]), "total_fees": float(position["total_fees"]),
                             "current_price": float(latest), "current_value": float(current_value),
                             "unrealized_gain": float(current_value - position["total_cost_basis"]),
                             "realized_gain": float(position["realized_gain"]), "dividend_gain": float(gain),
                             "dividend_fees": float(fees)})
        totals = self.compute_totals(portfolio_id)
        return {"portfolio_id": str(portfolio_id) if portfolio_id else None, "as_of": totals["as_of"],
                "currency": "PHP", "entities": entities,
                "totals": {key: value for key, value in totals.items() if key.startswith("total_")}}
__all__ = ["TotalsService"]
