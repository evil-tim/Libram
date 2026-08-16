# ruff: noqa: DTZ001, DTZ005
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from libram_types.libram_types import PriceRecord
from price_management.datasource import BaseDatasource
from price_management.service import PriceManagerService, _load_datasource


class FakeDatasource(BaseDatasource):
    last_instance = None

    def __init__(self, config):
        super().__init__(config)
        self.prices = self.config.get("prices", [])
        self.calls = []
        FakeDatasource.last_instance = self

    def fetch_prices(self, entity, start, end):
        self.calls.append((entity, start, end))
        return self.prices


class FakeDb:
    def __init__(self, entity, datasource=None, prices_count=0):
        self.entity = entity
        self.datasource = datasource
        self.prices_count = prices_count
        self.saved = None

    def get_entity_by_id_raw(self, entity_id):
        return self.entity if self.entity["id"] == entity_id else None

    def get_entity_by_code_raw(self, code):
        return self.entity if self.entity.get("code") == code else None

    def count_prices(self, entity_id, start, end):
        return self.prices_count

    def get_datasource_raw(self, datasource_id):
        return self.datasource

    def save_prices(self, entity_id, prices):
        self.saved = (entity_id, prices)
        return len(prices)


def make_entity(**overrides):
    value = {
        "id": uuid4(),
        "code": "ABC",
        "frequency": "DAILY",
        "has_weekend": False,
        "datasource_id": uuid4(),
        "config": {"entity": "wins"},
    }
    value.update(overrides)
    return value


def test_load_datasource_supports_explicit_and_default_class(monkeypatch):
    module = SimpleNamespace(Loaded=FakeDatasource, Datasource=FakeDatasource)
    monkeypatch.setattr(
        "price_management.service.importlib.import_module", lambda _: module
    )
    assert isinstance(_load_datasource("module:Loaded", {"x": 1}), FakeDatasource)
    assert isinstance(_load_datasource("module", {}), FakeDatasource)


def test_load_datasource_rejects_non_datasource(monkeypatch):
    class NotDatasource:
        def __init__(self, config):
            pass

    monkeypatch.setattr(
        "price_management.service.importlib.import_module",
        lambda _: SimpleNamespace(Datasource=NotDatasource),
    )
    with pytest.raises(TypeError, match="must subclass"):
        _load_datasource("module", {})


def test_expected_price_count_excludes_weekends():
    service = PriceManagerService(FakeDb(make_entity()))
    start = datetime(2026, 8, 1)  # Saturday
    end = datetime(2026, 8, 10)
    assert service._expected_price_count("DAILY", False, start, end) == 5
    assert service._expected_price_count("DAILY", True, start, end) == 9
    assert service._expected_price_count("CONTINUOUS", False, start, end) is None


def test_prices_exist_uses_count_and_continuous_always_fetches():
    entity = make_entity()
    service = PriceManagerService(FakeDb(entity, prices_count=5))
    start, end = datetime(2026, 8, 3), datetime(2026, 8, 10)
    assert service._prices_exist(entity, start, end) is True
    assert (
        service._prices_exist({**entity, "frequency": "CONTINUOUS"}, start, end)
        is False
    )


def test_fetch_and_store_merges_config_and_discards_future_prices(monkeypatch):
    entity = make_entity(config={"entity": "wins", "shared": "entity"})
    now = datetime.now()
    valid = PriceRecord(price=Decimal(10), timestamp=now.replace(microsecond=0))
    future = PriceRecord(price=Decimal(20), timestamp=datetime(2099, 1, 1))
    datasource_row = {
        "implementation": f"{__name__}:FakeDatasource",
        "config": {"shared": "datasource", "source": "api", "prices": [valid, future]},
    }
    db = FakeDb(entity, datasource_row)
    service = PriceManagerService(db)
    monkeypatch.setattr(service, "_prices_exist", lambda *_: False)
    inserted = service.fetch_and_store(
        entity["id"], None, datetime(2026, 1, 1), datetime(2026, 1, 2)
    )
    assert inserted == 1
    assert db.saved == (entity["id"], [valid])
    assert FakeDatasource.last_instance is not None
    assert FakeDatasource.last_instance.config["shared"] == "entity"
    assert FakeDatasource.last_instance.config["source"] == "api"


def test_fetch_and_store_falls_back_to_code_and_reports_missing_entity():
    entity = make_entity()
    service = PriceManagerService(FakeDb(entity))
    with pytest.raises(ValueError, match="entity not found"):
        service.fetch_and_store(None, "NOPE", datetime.now(), datetime.now())


def test_fetch_and_store_validates_datasource_configuration():
    entity = make_entity(datasource_id=None)
    service = PriceManagerService(FakeDb(entity))
    service._prices_exist = lambda *_: False
    with pytest.raises(ValueError, match="no datasource_id"):
        service.fetch_and_store(entity["id"], None, datetime.now(), datetime.now())

    entity = make_entity(datasource_id="not-a-uuid")
    service = PriceManagerService(FakeDb(entity))
    service._prices_exist = lambda *_: False
    with pytest.raises(TypeError, match="not a UUID"):
        service.fetch_and_store(entity["id"], None, datetime.now(), datetime.now())


def test_query_prices_requires_existing_entity():
    entity = make_entity()
    service = PriceManagerService(FakeDb(entity))
    with pytest.raises(ValueError, match="entity not found"):
        service.query_prices(uuid4(), datetime.now(), datetime.now())


def test_base_datasource_normalizes_none_config():
    datasource = FakeDatasource(None)
    assert datasource.config == {}
