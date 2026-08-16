# ruff: noqa: DTZ001
from datetime import datetime
from uuid import uuid4

import pytest

from libram_types.libram_types import TaskRecord
from price_scheduler.executor import PriceSchedulerExecutor


class Db:
    def __init__(self, task):
        self.task = task
        self.completed = []
        self.failed = []

    def find_and_lock_next_task(self, *args):
        return self.task

    def complete_task(self, task_id):
        self.completed.append(task_id)

    def fail_task(self, task_id, max_retries):
        self.failed.append((task_id, max_retries))


class Prices:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def fetch_and_store(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error


def task(start=datetime(2026, 1, 1), end=datetime(2026, 1, 2)):
    return TaskRecord(uuid4(), uuid4(), start, end)


def test_execute_task_completes_after_fetch():
    record = task()
    db, prices = Db(record), Prices()
    PriceSchedulerExecutor(prices, db, max_retries=3).execute_task()
    assert prices.calls[0]["entity_id"] == record.entity_id
    assert db.completed == [record.id]
    assert db.failed == []


def test_execute_task_marks_failure_for_retryable_error():
    record = task()
    db = Db(record)
    PriceSchedulerExecutor(
        Prices(RuntimeError("boom")), db, max_retries=4
    ).execute_task()
    assert db.failed == [(record.id, 4)]
    assert db.completed == []


@pytest.mark.parametrize(
    "start,end", [(None, datetime(2026, 1, 2)), (datetime(2026, 1, 1), None)]
)
def test_execute_task_rejects_incomplete_range(start, end):
    db = Db(task(start, end))
    with pytest.raises(ValueError, match="timestamp"):
        PriceSchedulerExecutor(Prices(), db).execute_task()


def test_execute_task_returns_when_no_task():
    db = Db(None)
    PriceSchedulerExecutor(Prices(), db).execute_task()
    assert db.completed == []


def test_stop_wakes_worker_and_signal_handler_stops():
    executor = PriceSchedulerExecutor(Prices(), Db(None))
    executor.handle_stop(None, None)
    assert executor.stop_event.is_set()
