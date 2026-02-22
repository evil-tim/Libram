from datetime import datetime, timedelta
from typing import Iterable, List, Optional, Tuple
from uuid import UUID
from zoneinfo import ZoneInfo

from price_management import PriceManagerClient, EntityRecord, PriceRecord

from .types import TaskRecord


def _month_range_for(dt: datetime) -> Tuple[datetime, datetime]:
    start = datetime(dt.year, dt.month, 1, tzinfo=dt.tzinfo)
    if dt.month == 12:
        end = datetime(dt.year + 1, 1, 1, tzinfo=dt.tzinfo)
    else:
        end = datetime(dt.year, dt.month + 1, 1, tzinfo=dt.tzinfo)
    return start, end


def _prev_month(dt: datetime) -> datetime:
    if dt.month == 1:
        return datetime(dt.year - 1, 12, 1, tzinfo=dt.tzinfo)
    return datetime(dt.year, dt.month - 1, 1, tzinfo=dt.tzinfo)


def _month_has_missing_prices(prices: Iterable[PriceRecord], start: datetime, end: datetime, has_weekend: bool) -> bool:
    present = set()
    for p in prices:
        ts = p.timestamp or p.timestamp_start
        if ts is None:
            continue
        present.add(ts.date())

    d = start.date()
    while d < end.date():
        if has_weekend:
            expect_date_present = True
        else:
            expect_date_present = d.weekday() < 5

        if expect_date_present and d not in present:
            return True

        d = d + timedelta(days=1)

    return False

class PriceSchedulerClient:
    """Client that generates monthly tasks for missing daily prices.

    Construct with a DSN string; a `Database` is created internally.
    """

    def __init__(self, price_manager_client: PriceManagerClient):
        self.price_manager_client = price_manager_client

    def generate_monthly_tasks(self, entity_id: Optional[UUID] = None, min_date: Optional[datetime] = None) -> Iterable[TaskRecord]:
        created: List[TaskRecord] = []

        entities = self.price_manager_client.query_entities(
            entity_id,
            None,
            None,
            "DAILY")

        for entity in entities:
            # count OPEN tasks for the entity
            open_count = 0
            # TODO: get open task count for entity from database

            # quit if there are already 2 or more OPEN tasks for the entity to avoid creating too many tasks for the same entity
            if open_count >= 2:
                continue

            # get the previous month range as a starting point for scanning
            # fail fast if the entity has an invalid timezone configured
            now = datetime.now(ZoneInfo(entity.timezone) if entity.timezone else None)
            scan = _prev_month(now)

            # determine the stop date for scanning
            stop_date = min_date or entity.min_timestamp
            if stop_date is None:
                stop_date = datetime(2000, 1, 1)  # arbitrary default stop date if none provided
            stop_date = stop_date.date()

            while True:
                # get date range for the month to scan
                # start date is inclusive, end date is exclusive
                month_start, month_end = _month_range_for(scan)

                # stop date is inclusive - quit if the scan window is now outside the stop date
                if month_end.date() < stop_date:
                    break

                # get the list of prices for the month
                prices = self.price_manager_client.query_prices(entity.id, month_start, month_end)

                # check if there are missing prices for the month
                if _month_has_missing_prices(prices, month_start, month_end, entity.has_weekend):
                    # check if there is already a task for the entity and month range

                    current_task = None
                    # TODO: get the task for the entity and month range from database

                    if not current_task:
                        # create a new task for the entity and month range
                        # TODO: insert a new task into the database with status OPEN and get the created task record
                        # tr = TaskRecord(
                        #    id=row["id"],
                        #    entity_id=row["entity_id"],
                        #    timestamp_start=row["timestamp_start"],
                        #    timestamp_end=row["timestamp_end"],
                        #    status=row["status"],
                        #    retry_count=row["retry_count"],
                        #    created_at=row["created_at"],
                        #)
                        # created.append(tr)
                        break
                    else:
                        if current_task.status == "OPEN":
                            # if there is already an OPEN task for the entity and month range, skip creating a new task
                            break
                # if there are no missing prices for the month
                # OR if there is a task for the month range but it's not OPEN
                # continue scanning previous months to find the previous one with no task or an OPEN task
                scan = _prev_month(scan)


        return created