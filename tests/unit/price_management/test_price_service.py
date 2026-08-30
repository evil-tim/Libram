# ruff: noqa: DTZ001, DTZ005
from datetime import UTC, datetime, timedelta, timezone
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


class FakeSnapshotDatasource(BaseDatasource):
    last_instance = None

    def __init__(self, config):
        super().__init__(config)
        self.snapshot = self.config.get("snapshot")
        self.calls = []
        FakeSnapshotDatasource.last_instance = self

    def fetch_price(self, entity):
        self.calls.append(entity)
        return self.snapshot


class FakeDb:
    def __init__(self, entity, datasource=None, prices_count=0):
        self.entity = entity
        self.datasource = datasource
        self.prices_count = prices_count
        self.saved = None
        self.calls = []

    def get_entity_by_id_raw(self, entity_id):
        self.calls.append(("get_entity_by_id_raw", entity_id))
        return self.entity if self.entity["id"] == entity_id else None

    def get_entity_by_code_raw(self, code):
        self.calls.append(("get_entity_by_code_raw", code))
        return self.entity if self.entity.get("code") == code else None

    def count_prices(self, entity_id, start, end):
        self.calls.append(("count_prices", entity_id, start, end))
        return self.prices_count

    def get_datasource_raw(self, datasource_id):
        self.calls.append(("get_datasource_raw", datasource_id))
        if self.datasource is None or not self.entity:
            return None
        if datasource_id != self.entity.get("datasource_id"):
            return None
        return self.datasource

    def save_prices(self, entity_id, prices):
        self.calls.append(("save_prices", entity_id, prices))
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
    db = FakeDb(entity, prices_count=5)
    service = PriceManagerService(db)
    start, end = datetime(2026, 8, 3), datetime(2026, 8, 10)
    assert service._prices_exist(entity, start, end) is True
    assert db.calls == [("count_prices", entity["id"], start, end)]
    assert (
        service._prices_exist({**entity, "frequency": "CONTINUOUS"}, start, end)
        is False
    )
    assert db.calls == [("count_prices", entity["id"], start, end)]


def test_fetch_and_store_skips_datasource_when_prices_already_exist():
    entity = make_entity()
    db = FakeDb(entity, datasource={}, prices_count=1)
    service = PriceManagerService(db)

    assert (
        service.fetch_and_store(
            entity["id"], None, datetime(2026, 8, 3), datetime(2026, 8, 4)
        )
        == 0
    )
    assert db.saved is None
    assert db.calls == [
        ("get_entity_by_id_raw", entity["id"]),
        ("count_prices", entity["id"], datetime(2026, 8, 3), datetime(2026, 8, 4)),
    ]


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
    inserted = service.fetch_and_store(
        entity["id"], None, datetime(2026, 1, 1), datetime(2026, 1, 2)
    )
    assert inserted == 1
    assert db.saved == (entity["id"], [valid])
    assert FakeDatasource.last_instance is not None
    assert FakeDatasource.last_instance.config["shared"] == "entity"
    assert FakeDatasource.last_instance.config["source"] == "api"
    assert db.calls[:3] == [
        ("get_entity_by_id_raw", entity["id"]),
        ("count_prices", entity["id"], datetime(2026, 1, 1), datetime(2026, 1, 2)),
        ("get_datasource_raw", entity["datasource_id"]),
    ]


def test_fetch_and_store_falls_back_to_code_and_reports_missing_entity():
    entity = make_entity()
    service = PriceManagerService(FakeDb(entity))
    with pytest.raises(ValueError, match="entity not found"):
        service.fetch_and_store(None, "NOPE", datetime.now(), datetime.now())


def test_fetch_and_store_validates_datasource_configuration():
    entity = make_entity(datasource_id=None)
    db = FakeDb(entity)
    service = PriceManagerService(db)
    with pytest.raises(ValueError, match="no datasource_id"):
        service.fetch_and_store(
            entity["id"], None, datetime(2026, 8, 3), datetime(2026, 8, 4)
        )

    entity = make_entity(datasource_id="not-a-uuid")
    service = PriceManagerService(FakeDb(entity))
    with pytest.raises(TypeError, match="not a UUID"):
        service.fetch_and_store(
            entity["id"], None, datetime(2026, 8, 3), datetime(2026, 8, 4)
        )


def test_query_prices_requires_existing_entity():
    entity = make_entity()
    service = PriceManagerService(FakeDb(entity))
    with pytest.raises(ValueError, match="entity not found"):
        service.query_prices(uuid4(), datetime.now(), datetime.now())


def test_base_datasource_normalizes_none_config():
    datasource = FakeDatasource(None)
    assert datasource.config == {}


def make_snapshot_datasource_row(snapshot):
    return {
        "implementation": f"{__name__}:FakeSnapshotDatasource",
        "config": {"snapshot": snapshot},
    }


def make_snapshot(price=Decimal(100), timestamp=None, **overrides):
    record = PriceRecord(price=price, timestamp=timestamp, **overrides)
    return record


def test_fetch_snapshot_and_store_normalizes_and_saves():
    entity = make_entity()
    ts = datetime.now(UTC).astimezone(timezone(timedelta(hours=8)))
    snapshot = make_snapshot(timestamp=ts)
    db = FakeDb(entity, make_snapshot_datasource_row(snapshot))
    service = PriceManagerService(db)

    assert service.fetch_snapshot_and_store(entity["id"], None) == 1
    saved_entity_id, saved_prices = db.saved
    assert saved_entity_id == entity["id"]
    assert len(saved_prices) == 1
    saved = saved_prices[0]
    assert saved.timestamp.tzinfo == UTC
    assert saved.timestamp == ts.astimezone(UTC)
    assert FakeSnapshotDatasource.last_instance.calls == [entity]
    assert db.calls[:2] == [
        ("get_entity_by_id_raw", entity["id"]),
        ("get_datasource_raw", entity["datasource_id"]),
    ]


def test_fetch_snapshot_and_store_dry_run_does_not_save(capsys):
    entity = make_entity()
    snapshot = make_snapshot(timestamp=datetime.now(UTC))
    db = FakeDb(entity, make_snapshot_datasource_row(snapshot))
    service = PriceManagerService(db)

    assert service.fetch_snapshot_and_store(entity["id"], None, dry_run=True) == 0
    assert db.saved is None
    assert not any(call[0] == "save_prices" for call in db.calls)
    assert "Got the following price" in capsys.readouterr().out


def test_fetch_snapshot_and_store_resolves_by_code():
    entity = make_entity()
    snapshot = make_snapshot(timestamp=datetime.now(UTC))
    db = FakeDb(entity, make_snapshot_datasource_row(snapshot))
    service = PriceManagerService(db)

    assert service.fetch_snapshot_and_store(None, "ABC") == 1
    assert db.saved[0] == entity["id"]


def test_fetch_snapshot_and_store_reports_missing_entity():
    entity = make_entity()
    service = PriceManagerService(FakeDb(entity))
    with pytest.raises(ValueError, match="entity not found"):
        service.fetch_snapshot_and_store(None, "NOPE")


def test_fetch_snapshot_and_store_validates_datasource_configuration():
    entity = make_entity(datasource_id=None)
    with pytest.raises(ValueError, match="no datasource_id"):
        PriceManagerService(FakeDb(entity)).fetch_snapshot_and_store(entity["id"], None)

    entity = make_entity(datasource_id="not-a-uuid")
    with pytest.raises(TypeError, match="not a UUID"):
        PriceManagerService(FakeDb(entity)).fetch_snapshot_and_store(entity["id"], None)

    entity = make_entity()
    db = FakeDb(entity, None)
    with pytest.raises(ValueError, match="datasource not found"):
        PriceManagerService(db).fetch_snapshot_and_store(entity["id"], None)

    entity = make_entity()
    db = FakeDb(entity, {"config": {}})
    with pytest.raises(ValueError, match="implementation not specified"):
        PriceManagerService(db).fetch_snapshot_and_store(entity["id"], None)


def test_fetch_snapshot_and_store_rejects_missing_price_or_timestamp():
    entity = make_entity()
    service = PriceManagerService(
        FakeDb(
            entity,
            make_snapshot_datasource_row(
                make_snapshot(price=None, timestamp=datetime.now(UTC))
            ),
        )
    )
    with pytest.raises(ValueError, match="price and timestamp"):
        service.fetch_snapshot_and_store(entity["id"], None)

    snapshot = make_snapshot(timestamp=None)
    service = PriceManagerService(
        FakeDb(entity, make_snapshot_datasource_row(snapshot))
    )
    with pytest.raises(ValueError, match="price and timestamp"):
        service.fetch_snapshot_and_store(entity["id"], None)


def test_fetch_snapshot_and_store_rejects_ohlc_record():
    entity = make_entity()
    snapshot = make_snapshot(
        timestamp=datetime.now(UTC),
        timestamp_start=datetime.now(UTC) - timedelta(hours=1),
    )
    service = PriceManagerService(
        FakeDb(entity, make_snapshot_datasource_row(snapshot))
    )
    with pytest.raises(ValueError, match="single-timestamp"):
        service.fetch_snapshot_and_store(entity["id"], None)


def test_fetch_snapshot_and_store_rejects_naive_timestamp():
    entity = make_entity()
    snapshot = make_snapshot(timestamp=datetime.now())
    service = PriceManagerService(
        FakeDb(entity, make_snapshot_datasource_row(snapshot))
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        service.fetch_snapshot_and_store(entity["id"], None)


def test_fetch_snapshot_and_store_rejects_future_timestamp():
    entity = make_entity()
    snapshot = make_snapshot(timestamp=datetime.now(UTC) + timedelta(hours=1))
    service = PriceManagerService(
        FakeDb(entity, make_snapshot_datasource_row(snapshot))
    )
    with pytest.raises(ValueError, match="cannot be in the future"):
        service.fetch_snapshot_and_store(entity["id"], None)


def test_fetch_snapshot_and_store_rejects_non_price_record():
    entity = make_entity()
    db = FakeDb(entity, make_snapshot_datasource_row({"price": Decimal(1)}))
    service = PriceManagerService(db)
    with pytest.raises(TypeError, match="must return a PriceRecord"):
        service.fetch_snapshot_and_store(entity["id"], None)


def test_fetch_and_store_dry_run_does_not_save(capsys):
    entity = make_entity()
    now = datetime.now()
    valid = PriceRecord(price=Decimal(10), timestamp=now.replace(microsecond=0))
    future = PriceRecord(price=Decimal(20), timestamp=datetime(2099, 1, 1))
    datasource_row = {
        "implementation": f"{__name__}:FakeDatasource",
        "config": {"prices": [valid, future]},
    }
    db = FakeDb(entity, datasource_row)
    service = PriceManagerService(db)

    assert (
        service.fetch_and_store(
            entity["id"], None, datetime(2026, 1, 1), datetime(2026, 1, 2), dry_run=True
        )
        == 0
    )
    assert db.saved is None
    assert not any(call[0] == "save_prices" for call in db.calls)
    output = capsys.readouterr().out
    assert "Got the following prices" in output
    # future record still filtered out before the dry-run short-circuit
    assert repr(future.timestamp) not in output
    assert repr(valid.timestamp) in output


def test_fetch_and_store_dry_run_skips_fetch_when_prices_exist():
    entity = make_entity()
    db = FakeDb(entity, datasource={}, prices_count=1)
    service = PriceManagerService(db)

    assert (
        service.fetch_and_store(
            entity["id"], None, datetime(2026, 8, 3), datetime(2026, 8, 4), dry_run=True
        )
        == 0
    )
    assert db.saved is None
    assert not any(call[0] == "save_prices" for call in db.calls)


def test_fetch_and_store_discards_future_ohlc_prices():
    entity = make_entity()
    now = datetime.now()
    valid_ohlc = PriceRecord(
        price=Decimal(10),
        timestamp_start=now - timedelta(days=1),
        timestamp_end=now - timedelta(hours=1),
    )
    future_ohlc = PriceRecord(
        price=Decimal(20),
        timestamp_start=now - timedelta(hours=1),
        timestamp_end=now + timedelta(hours=1),
    )
    datasource_row = {
        "implementation": f"{__name__}:FakeDatasource",
        "config": {"prices": [valid_ohlc, future_ohlc]},
    }
    db = FakeDb(entity, datasource_row)
    service = PriceManagerService(db)

    assert (
        service.fetch_and_store(
            entity["id"], None, datetime(2026, 1, 1), datetime(2026, 1, 2)
        )
        == 1
    )
    assert db.saved == (entity["id"], [valid_ohlc])
