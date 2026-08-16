from decimal import Decimal
from uuid import uuid4

import pytest

from portfolio_management import (
    DividendNotFound,
    PortfolioDividendCreateRequest,
    PortfolioDividendNotFound,
    PortfolioNotFound,
    PortfolioValidationError,
)
from portfolio_management.dividend_fees import DividendFeeService


class Db:
    def __init__(self, portfolio=True, event=True, record=None):
        self.portfolio = portfolio
        self.event = event
        self.record = record

    def get_portfolio(self, _):
        return object() if self.portfolio else None

    def get_dividend_event(self, _):
        return object() if self.event else None

    def get_portfolio_dividend(self, *_):
        return self.record

    def create_portfolio_dividend(self, **kwargs):
        return type(
            "Record",
            (),
            {**kwargs, "id": uuid4(), "created_at": None, "updated_at": None},
        )()

    def get_entity_by_id_raw(self, _):
        return {"code": "USD"}


def test_get_requires_portfolio_and_event_scope():
    pid, eid = uuid4(), uuid4()
    with pytest.raises(PortfolioNotFound):
        DividendFeeService(object(), Db(portfolio=False)).get(pid, eid)
    with pytest.raises(DividendNotFound):
        DividendFeeService(object(), Db(event=False)).get(pid, eid)
    with pytest.raises(PortfolioDividendNotFound):
        DividendFeeService(object(), Db()).get(pid, eid)


def test_create_rejects_duplicate_association_and_unknown_currency():
    pid, eid = uuid4(), uuid4()
    body = PortfolioDividendCreateRequest(fees=Decimal(2), fees_entity_code="USD")
    with pytest.raises(PortfolioValidationError, match="already exists"):
        DividendFeeService(object(), Db(record=object())).create(pid, eid, body)

    class Prices:
        def query_entities(self, *args):
            return []

    body = PortfolioDividendCreateRequest(fees_entity_code="NOPE")
    with pytest.raises(PortfolioValidationError, match="entity not found"):
        DividendFeeService(Prices(), Db()).create(pid, eid, body)


def test_create_formats_currency_code():
    pid, eid, currency_id = uuid4(), uuid4(), uuid4()
    body = PortfolioDividendCreateRequest(fees=Decimal(2), fees_entity_code="USD")

    class Prices:
        def query_entities(self, *args):
            return [type("Entity", (), {"id": currency_id})()]

    result = DividendFeeService(Prices(), Db()).create(pid, eid, body)
    assert result["fees"] == Decimal(2)
    assert result["fees_entity_code"] == "USD"
    assert result["portfolio_id"] == pid
