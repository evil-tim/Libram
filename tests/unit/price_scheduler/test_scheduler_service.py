# ruff: noqa: DTZ001
from datetime import UTC, datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from libram_types.libram_types import EntityRecord, TaskRecord
from price_scheduler.service import PriceSchedulerService


class StrictManagerSpy:
    def __init__(self, entities, prices=(), entity_filter=None):
        self.entities = entities
        self.prices = prices
        self.entity_filter = entity_filter
        self.entity_queries = []
        self.price_queries = []

    def query_entities(self, entity_id, entity_code, entity_name, frequency):
        query = (entity_id, entity_code, entity_name, frequency)
        self.entity_queries.append(query)
        assert query == (self.entity_filter, None, None, "DAILY")
        return self.entities

    def query_prices(self, entity_id, start, end, page, size):
        assert isinstance(entity_id, UUID)
        assert start < end
        assert page == 0
        assert size == 31
        self.price_queries.append((entity_id, start, end))
        return self.prices


class StrictDbSpy:
    def __init__(self, open_count=0, existing=None, expected_ranges=()):
        self.open_count = open_count
        self.existing = existing
        self.expected_ranges = set(expected_ranges)
        self.task_count_queries = []
        self.range_queries = []
        self.created = []

    def count_tasks(self, entity_id, status):
        assert isinstance(entity_id, UUID)
        assert status == "OPEN"
        self.task_count_queries.append((entity_id, status))
        return self.open_count

    def get_task_for_range(self, entity_id, start, end):
        assert isinstance(entity_id, UUID)
        assert start < end
        query = (entity_id, start, end)
        self.range_queries.append(query)
        if self.expected_ranges:
            assert query in self.expected_ranges
        return self.existing

    def create_new_task(self, entity_id, start, end):
        assert isinstance(entity_id, UUID)
        assert start < end
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


# ZoneInfo("UTC") reports +08:00 in this container's defective zone database, so
# no expectation below is built by converting between zones: every value is
# compared against the same zone object the fixture used, which keeps these
# tests portable to a healthy container. Ceilings are built with datetime.UTC, a
# fixed-offset alias rather than a ZoneInfo, so they are unaffected. The Manila
# case is the one that pins the conversion, and it depends only on Asia/Manila.
UTC_ZONE = ZoneInfo("UTC")
MANILA = ZoneInfo("Asia/Manila")


def freeze_now(monkeypatch, year, month, day, hour=12):
    """Pin price_scheduler.service.datetime so that `now` is a fixed local instant."""

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(year, month, day, hour, tzinfo=tz)

    monkeypatch.setattr("price_scheduler.service.datetime", FixedDateTime)


def test_monthly_generation_skips_entity_at_open_task_limit():
    entity = daily_entity()
    manager = StrictManagerSpy([entity])
    db = StrictDbSpy(open_count=1)
    service = PriceSchedulerService(manager, db)
    assert list(service.generate_monthly_tasks(max_open_tasks=1)) == []
    assert manager.entity_queries == [(None, None, None, "DAILY")]
    assert db.task_count_queries == [(entity.id, "OPEN")]
    assert manager.price_queries == []


def test_daily_generation_creates_missing_weekday_task(monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 12, 12, tzinfo=tz)

    monkeypatch.setattr("price_scheduler.service.datetime", FixedDateTime)
    entity = daily_entity(min_timestamp=None)
    expected_range = (
        entity.id,
        datetime(2026, 8, 11, tzinfo=ZoneInfo("UTC")),
        datetime(2026, 8, 12, tzinfo=ZoneInfo("UTC")),
    )
    manager = StrictManagerSpy([entity], prices=[])
    db = StrictDbSpy(expected_ranges=[expected_range])
    created = list(
        PriceSchedulerService(manager, db).generate_daily_tasks(max_open_tasks=1)
    )
    assert len(created) == 1
    assert created[0].status == "OPEN"
    assert created[0].timestamp_end > created[0].timestamp_start
    assert manager.entity_queries == [(None, None, None, "DAILY")]
    assert manager.price_queries == [expected_range]
    assert db.range_queries == [expected_range]


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
    expected_range = (
        entity.id,
        datetime(2026, 7, 1, tzinfo=ZoneInfo("UTC")),
        datetime(2026, 8, 1, tzinfo=ZoneInfo("UTC")),
    )
    db = StrictDbSpy(existing=existing, expected_ranges=[expected_range])
    manager = StrictManagerSpy([entity], prices=[])
    assert (
        list(
            PriceSchedulerService(manager, db).generate_monthly_tasks(max_open_tasks=1)
        )
        == []
    )
    assert db.created == []
    assert manager.entity_queries == [(None, None, None, "DAILY")]
    assert db.range_queries == [expected_range]


def test_strict_fakes_reject_wrong_filter_and_range():
    entity = daily_entity()
    manager = StrictManagerSpy([entity])
    db = StrictDbSpy(
        expected_ranges=[(entity.id, datetime(2026, 8, 1), datetime(2026, 9, 1))]
    )

    with pytest.raises(AssertionError):
        manager.query_entities(entity.id, None, None, "WEEKLY")
    with pytest.raises(AssertionError):
        db.get_task_for_range(entity.id, datetime(2026, 8, 2), datetime(2026, 9, 2))


def test_daily_ceiling_older_than_the_scan_window_creates_nothing(monkeypatch):
    # now is Wed 2026-08-12, so the daily scan only ever reaches back to 2026-08-03
    freeze_now(monkeypatch, 2026, 8, 12)
    entity = daily_entity(
        min_timestamp=None, max_timestamp=datetime(2026, 7, 10, tzinfo=UTC)
    )
    manager = StrictManagerSpy([entity], prices=[])
    db = StrictDbSpy()

    created = list(
        PriceSchedulerService(manager, db).generate_daily_tasks(max_open_tasks=1)
    )

    assert created == []
    assert manager.price_queries == []
    assert db.created == []


def test_daily_ceiling_inside_the_scan_window_starts_at_the_ceiling(monkeypatch):
    freeze_now(monkeypatch, 2026, 8, 12)
    entity = daily_entity(
        min_timestamp=None, max_timestamp=datetime(2026, 8, 10, tzinfo=UTC)
    )
    manager = StrictManagerSpy([entity], prices=[])
    db = StrictDbSpy()

    created = list(
        PriceSchedulerService(manager, db).generate_daily_tasks(max_open_tasks=1)
    )

    # yesterday would be 2026-08-11; the ceiling pulls the scan back to 2026-08-10
    assert [(q[1], q[2]) for q in manager.price_queries] == [
        (datetime(2026, 8, 10, tzinfo=UTC_ZONE), datetime(2026, 8, 11, tzinfo=UTC_ZONE))
    ]
    assert created[0].timestamp_start == datetime(2026, 8, 10, tzinfo=UTC_ZONE)


def test_monthly_ceiling_scan_starts_at_the_ceiling_month(monkeypatch):
    # now is 2026-09-12, so an unclamped scan would start at August 2026
    freeze_now(monkeypatch, 2026, 9, 12)
    entity = daily_entity(
        min_timestamp=datetime(2020, 1, 1),
        max_timestamp=datetime(2026, 7, 10, tzinfo=UTC),
    )
    manager = StrictManagerSpy([entity], prices=[])
    db = StrictDbSpy()

    created = list(
        PriceSchedulerService(manager, db).generate_monthly_tasks(max_open_tasks=1)
    )

    # the window containing the ceiling is admitted whole, not clamped to it
    assert [(q[1], q[2]) for q in manager.price_queries] == [
        (datetime(2026, 7, 1, tzinfo=UTC_ZONE), datetime(2026, 8, 1, tzinfo=UTC_ZONE))
    ]
    assert created[0].timestamp_start == datetime(2026, 7, 1, tzinfo=UTC_ZONE)
    assert created[0].timestamp_end == datetime(2026, 8, 1, tzinfo=UTC_ZONE)


def test_monthly_ceiling_before_the_floor_creates_nothing(monkeypatch):
    freeze_now(monkeypatch, 2026, 9, 12)
    entity = daily_entity(
        min_timestamp=datetime(2026, 8, 1),
        max_timestamp=datetime(2026, 6, 15, tzinfo=UTC),
    )
    manager = StrictManagerSpy([entity], prices=[])
    db = StrictDbSpy()

    created = list(
        PriceSchedulerService(manager, db).generate_monthly_tasks(max_open_tasks=1)
    )

    # the ceiling's month ends 2026-07-01, before the floor, so the existing
    # stop-date break fires on the first window. An inverted pair like this one
    # is rejected by chk_entity_timestamp_bounds, so the state is unreachable
    # through the database; this pins the generator's own guard.
    assert created == []
    assert manager.price_queries == []


def test_weekly_ceiling_older_than_the_stop_date_creates_nothing(monkeypatch):
    # now is Sat 2026-09-12; the weekly scan stops 30 days back, at 2026-08-13
    freeze_now(monkeypatch, 2026, 9, 12)
    entity = daily_entity(
        min_timestamp=None, max_timestamp=datetime(2026, 7, 10, tzinfo=UTC)
    )
    manager = StrictManagerSpy([entity], prices=[])
    db = StrictDbSpy()

    created = list(
        PriceSchedulerService(manager, db).generate_weekly_tasks(max_open_tasks=1)
    )

    assert created == []
    assert manager.price_queries == []


def test_monthly_ceiling_is_windowed_in_the_entity_timezone(monkeypatch):
    freeze_now(monkeypatch, 2026, 9, 12)
    # 2026-06-30T20:00Z is 2026-07-01T04:00 in Manila, so the ceiling's calendar
    # month differs by frame, and it is early enough to win the clamp: read raw
    # the scan would start in June, converted it starts in July.
    entity = daily_entity(
        timezone="Asia/Manila",
        min_timestamp=None,
        max_timestamp=datetime(2026, 6, 30, 20, tzinfo=UTC),
    )
    manager = StrictManagerSpy([entity], prices=[])
    db = StrictDbSpy()

    created = list(
        PriceSchedulerService(manager, db).generate_monthly_tasks(max_open_tasks=1)
    )

    assert [(q[1], q[2]) for q in manager.price_queries] == [
        (datetime(2026, 7, 1, tzinfo=MANILA), datetime(2026, 8, 1, tzinfo=MANILA))
    ]
    assert created[0].timestamp_start == datetime(2026, 7, 1, tzinfo=MANILA)
