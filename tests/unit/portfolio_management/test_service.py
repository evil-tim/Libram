from uuid import uuid4

from portfolio_management.service import PortfolioManagerService


class Child:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return (name, args, kwargs)

        return method


def make_manager():
    manager = PortfolioManagerService.__new__(PortfolioManagerService)
    manager.portfolio_service = Child()
    manager.order_service = Child()
    manager.totals_service = Child()
    manager.dividend_service = Child()
    manager.dividend_fee_service = Child()
    return manager


def test_dividend_methods_delegate_arguments_and_results():
    manager = make_manager()
    body, pid, eid = object(), uuid4(), uuid4()
    assert manager.create_dividend(body)[0] == "create"
    assert manager.list_dividends("ABC", "from", "to")[1] == ("ABC", "from", "to")
    assert manager.get_dividend(eid)[1] == (eid,)
    assert manager.update_dividend(eid, body)[1] == (eid, body)
    assert manager.delete_dividend(eid)[1] == (eid,)
    assert manager.create_dividend_fee(pid, eid, body)[1] == (pid, eid, body)
    assert manager.get_dividend_fee(pid, eid)[1] == (pid, eid)
    assert manager.update_dividend_fee(pid, eid, body)[1] == (pid, eid, body)
    assert manager.delete_dividend_fee(pid, eid)[1] == (pid, eid)
    assert manager.list_dividend_fees(pid)[1] == (pid,)


def test_portfolio_order_and_total_methods_preserve_keyword_wiring():
    manager = make_manager()
    pid, oid, body = uuid4(), uuid4(), object()
    manager.create_portfolio(body)
    manager.list_portfolios()
    manager.update_portfolio(pid, body)
    manager.delete_portfolio(pid)
    manager.create_order(pid, body)
    manager.update_order(pid, oid, body)
    manager.delete_order(oid)
    manager.list_orders(pid, 1, 2, "ABC", "buy", "a", "b", "shares", "asc")
    manager.compute_totals(pid)
    manager.compute_totals_by_entity(pid)
    assert manager.order_service.calls[2][1] == (oid,)
    assert manager.order_service.calls[3][2] == {
        "portfolio_id": pid,
        "page": 1,
        "size": 2,
        "entity_code": "ABC",
        "order_type": "buy",
        "date_from": "a",
        "date_to": "b",
        "sort_by": "shares",
        "sort_order": "asc",
    }
    assert manager.totals_service.calls[-2][1] == (pid,)
    assert manager.totals_service.calls[-1][1] == (pid,)
