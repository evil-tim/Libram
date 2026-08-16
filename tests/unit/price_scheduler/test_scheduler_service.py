# ruff: noqa: DTZ001
from datetime import datetime
from uuid import uuid4

from libram_types.libram_types import EntityRecord, TaskRecord
from price_scheduler.service import PriceSchedulerService


class FakeManager:
    def __init__(self, entities, prices=()):
        self.entities = entities
        self.prices = prices
        self.queries = []

    def query_entities(self, *args):
        return self.entities

    def query_prices(self, entity_id, start, end, **kwargs):
        self.queries.append((entity_id, start, end))
        return self.prices


class FakeDb:
    def __init__(self, open_count=0, existing=None):
        self.open_count = open_count
        self.existing = existing
        self.created = []

    def count_tasks(self, entity_id, status):
        return self.open_count

    def get_task_for_range(self, entity_id, start, end):
        return self.existing

    def create_new_task(self, entity_id, start, end):
        task = TaskRecord(uuid4(), entity_id, start, end, "OPEN")
        self.created.append(task)
        return task


def daily_entity(**kwargs):
    values = {
        "id": uuid4(),
        "code": "ABC",
        "name": "Alpha",
        "frequency": "DAILY",
        "timezone": "UTC",
        "min_timestamp": datetime(2026, 8, 1),
    }
    values.update(kwargs)
    return EntityRecord(**values)


def test_monthly_generation_skips_entity_at_open_task_limit():
    entity = daily_entity()
    manager = FakeManager([entity])
    db = FakeDb(open_count=1)
    service = PriceSchedulerService(manager, db)
    assert list(service.generate_monthly_tasks(max_open_tasks=1)) == []
    assert manager.queries == []


def test_daily_generation_creates_missing_weekday_task(monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 12, 12, tzinfo=tz)

    monkeypatch.setattr("price_scheduler.service.datetime", FixedDateTime)
    entity = daily_entity(min_timestamp=None)
    manager = FakeManager([entity], prices=[])
    db = FakeDb()
    created = list(
        PriceSchedulerService(manager, db).generate_daily_tasks(max_open_tasks=1)
    )
    assert len(created) == 1
    assert created[0].status == "OPEN"
    assert created[0].timestamp_end > created[0].timestamp_start
    assert len(manager.queries) == 1


def test_monthly_generation_does_not_duplicate_existing_task(monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 12, 12, tzinfo=tz)

    monkeypatch.setattr("price_scheduler.service.datetime", FixedDateTime)
    entity = daily_entity(min_timestamp=datetime(2026, 8, 1))
    existing = TaskRecord(
        uuid4(), entity.id, datetime(2026, 7, 1), datetime(2026, 8, 1), "FAILED"
    )
    db = FakeDb(existing=existing)
    manager = FakeManager([entity], prices=[])
    assert (
        list(
            PriceSchedulerService(manager, db).generate_monthly_tasks(max_open_tasks=1)
        )
        == []
    )
    assert db.created == []
