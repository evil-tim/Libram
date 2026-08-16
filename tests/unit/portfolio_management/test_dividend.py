from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from libram_types.libram_types import DividendEventRecord, EntityRecord
from portfolio_management import (
    DividendEventCreateRequest,
    DividendEventUpdateRequest,
    DividendNotFound,
    PortfolioValidationError,
)
from portfolio_management.dividend import DividendService


class Prices:
    def __init__(self, entities):
        self.entities = entities

    def query_entities(self, _type, code, _currency, _datasource):
        return [e for e in self.entities if e.code == code]


class Db:
    def __init__(self, record=None):
        self.record = record
        self.calls = []

    def get_entity_by_id_raw(self, entity_id):
        return {"code": "ABC", "name": "Alpha"} if entity_id else None

    def create_dividend_event(self, **kwargs):
        self.calls.append(("create", kwargs))
        self.record = DividendEventRecord(uuid4(), **kwargs)
        return self.record

    def list_dividend_events(self, *args):
        self.calls.append(("list", args))
        return [self.record] if self.record else []

    def get_dividend_event(self, _event_id):
        return self.record

    def update_dividend_event(self, _event_id, **values):
        self.calls.append(("update", values))
        return self.record

    def delete_dividend_event(self, _event_id):
        self.calls.append(("delete",))
        return self.record is not None


def make_service(record=None):
    entity = EntityRecord(uuid4(), "ABC", "Alpha")
    currency = EntityRecord(uuid4(), "PHP", "Peso")
    return DividendService(Prices([entity, currency]), Db(record)), entity, currency


def test_create_formats_record_and_resolves_optional_currency():
    service, entity, currency = make_service()
    body = DividendEventCreateRequest(
        entity_code="ABC",
        ex_date=date(2026, 1, 2),
        amount_per_share=Decimal("1.25"),
        amount_per_share_entity_code="PHP",
    )
    result = service.create(body)
    assert result["entity_id"] == entity.id
    assert result["entity_code"] == "ABC"
    assert result["amount_per_share_entity_id"] == currency.id
    assert result["amount_per_share_entity_code"] == "ABC"


def test_unknown_entity_is_validation_error_and_missing_get_is_not_found():
    service, _, _ = make_service()
    with pytest.raises(PortfolioValidationError, match="entity not found"):
        service.create(
            DividendEventCreateRequest(
                entity_code="NOPE", ex_date=datetime.now(UTC).date(), amount_per_share=1
            )
        )
    with pytest.raises(DividendNotFound):
        service.get(uuid4())


def test_update_maps_entity_codes_and_delete_translates_missing_record():
    service, entity, currency = make_service()
    service.db.record = DividendEventRecord(
        uuid4(), entity.id, date(2026, 1, 1), amount_per_share=Decimal(1)
    )
    result = service.update(
        service.db.record.id,
        DividendEventUpdateRequest(
            entity_code="PHP", amount_per_share_entity_code=None
        ),
    )
    assert result["entity_id"] == entity.id
    assert service.db.calls[-1][0] == "update"
    assert service.db.calls[-1][1]["entity_id"] == currency.id
    service.db.record = None
    with pytest.raises(DividendNotFound):
        service.delete(uuid4())


def test_list_filters_by_resolved_entity_and_formats_empty_currency():
    service, entity, _ = make_service()
    service.db.record = DividendEventRecord(
        uuid4(), entity.id, date(2026, 1, 1), amount_per_share=Decimal(2)
    )
    assert service.list("ABC")[0]["amount_per_share_entity_code"] is None
    assert service.list()[0]["entity_name"] == "Alpha"


def test_unknown_optional_currency_is_rejected():
    service, _, _ = make_service()
    body = DividendEventCreateRequest(
        entity_code="ABC",
        ex_date=datetime.now(UTC).date(),
        amount_per_share=1,
        amount_per_share_entity_code="NOPE",
    )
    with pytest.raises(PortfolioValidationError, match="entity not found"):
        service.create(body)


def test_update_missing_record_is_not_found_before_resolving_values():
    service, _, _ = make_service()
    with pytest.raises(DividendNotFound):
        service.update(uuid4(), DividendEventUpdateRequest(entity_code="NOPE"))


def test_delete_existing_record_succeeds():
    service, entity, _ = make_service()
    service.db.record = DividendEventRecord(
        uuid4(), entity.id, datetime.now(UTC).date()
    )
    service.delete(service.db.record.id)
    assert service.db.calls[-1][0] == "delete"
