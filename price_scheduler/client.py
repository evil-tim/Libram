from datetime import datetime, timedelta
from typing import Iterable, List, Optional, Tuple
from uuid import UUID
from zoneinfo import ZoneInfo

from libram_database.db import Database
from price_management import PriceManagerClient

from libram_types.libram_types import PriceRecord, TaskRecord


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
    print(f"Present price dates: {present}")
    d = start.date()
    while d < end.date():
        if has_weekend:
            expect_date_present = True
        else:
            expect_date_present = d.weekday() < 5

        print(f"Checking date {d} - expect present: {expect_date_present}, present: {d in present}")
        if expect_date_present and d not in present:
            return True

        d = d + timedelta(days=1)

    return False


def _week_range_for(dt: datetime) -> Tuple[datetime, datetime]:
    """Get the Monday-inclusive start and the following Monday-exclusive end of the week containing dt."""
    start = dt - timedelta(days=dt.weekday())
    start = datetime(start.year, start.month, start.day, tzinfo=dt.tzinfo)
    end = start + timedelta(days=7)
    return start, end


def _prev_week(dt: datetime) -> datetime:
    """Get the Monday of the previous week."""
    d = dt - timedelta(days=7)
    d = d - timedelta(days=d.weekday())
    return datetime(d.year, d.month, d.day, tzinfo=d.tzinfo)


def _day_has_missing_prices(prices: Iterable[PriceRecord], day_start: datetime, day_end: datetime, has_weekend: bool) -> bool:
    """Check if a specific day is missing prices."""
    # Skip check for weekends if has_weekend is False
    if not has_weekend and day_start.date().weekday() >= 5:
        return False

    for p in prices:
        ts = p.timestamp or p.timestamp_start
        if ts is None:
            continue
        if ts.date() == day_start.date():
            return False  # Price found for this day

    return True  # No price found for this day

class PriceSchedulerClient:
    """Client that generates monthly tasks for missing daily prices.

    Construct with a DSN string; a `Database` is created internally.
    """

    def __init__(self, price_manager_client: PriceManagerClient, db: Database):
        self.price_manager_client = price_manager_client
        self.db = db

    def generate_monthly_tasks(self, entity_id: Optional[UUID] = None, min_date: Optional[datetime] = None, max_open_tasks: int = 2000) -> Iterable[TaskRecord]:
        created: List[TaskRecord] = []

        entities = self.price_manager_client.query_entities(
            entity_id,
            None,
            None,
            "DAILY")

        for entity in entities:
            # count OPEN tasks for the entity
            open_count = self.db.count_tasks(entity.id, "OPEN")
            # quit if there are already 1 or more OPEN tasks for the entity to avoid creating too many tasks for the same entity
            if open_count >= max_open_tasks:
                print(f"Entity {entity.name} ({entity.code}) - {entity.id} has {open_count} OPEN tasks, skipping")
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

            print(f"Scanning entity {entity.name} ({entity.code}) - {entity.id} for missing prices starting from {scan.date()} back to {stop_date}")

            open_task_count = 0
            while True:
                # don't create more than max open tasks
                if open_task_count >= max_open_tasks:
                    break
                # get date range for the month to scan
                # start date is inclusive, end date is exclusive
                month_start, month_end = _month_range_for(scan)

                print(f"Scanning month {month_start.date()} to {month_end.date()}")

                # stop date is inclusive - quit if the scan window is now outside the stop date
                if month_end.date() < stop_date:
                    break

                # get the list of prices for the month
                prices = self.price_manager_client.query_prices(entity.id, month_start, month_end, page = 0, size = 31)

                # check if there are missing prices for the month
                if _month_has_missing_prices(prices, month_start, month_end, entity.has_weekend):
                    print(f"Month {month_start.date()} to {month_end.date()} has missing prices, checking for existing tasks")

                    # check if there is already a task for the entity and month range

                    current_task = self.db.get_task_for_range(entity.id, month_start, month_end)

                    if not current_task:
                        print(f"No existing task for month {month_start.date()} to {month_end.date()}, creating new task and continuing")
                        # create a new task for the entity and month range
                        tr = self.db.create_new_task(entity.id, month_start, month_end)
                        created.append(tr)
                        open_task_count += 1
                    else:
                        if current_task.status == "OPEN":
                            print(f"Existing OPEN task {current_task.id} for month {month_start.date()} to {month_end.date()}, continuing scan")
                            # if there is already an OPEN task for the entity and month range, skip creating a new task
                            open_task_count += 1
                        else:
                            print(f"Existing task {current_task.id} for month {month_start.date()} to {month_end.date()} is not OPEN, continuing scan")
                else:
                    print(f"Month {month_start.date()} to {month_end.date()} has no missing prices, continuing scan")
                # if there are no missing prices for the month
                # OR if there is a task for the month range but it's not OPEN
                # continue scanning previous months to find the previous one with no task or an OPEN task
                scan = _prev_month(scan)

        return created

    def generate_daily_tasks(self, entity_id: Optional[UUID] = None, max_open_tasks: int = 2000) -> Iterable[TaskRecord]:
        """Generate daily tasks for missing daily prices up to the previous week."""
        created: List[TaskRecord] = []

        entities = self.price_manager_client.query_entities(
            entity_id,
            None,
            None,
            "DAILY")

        for entity in entities:
            # count OPEN tasks for the entity
            open_count = self.db.count_tasks(entity.id, "OPEN")
            if open_count >= max_open_tasks:
                print(f"Entity {entity.name} ({entity.code}) - {entity.id} has {open_count} OPEN tasks, skipping")
                continue

            # get the previous week as a starting point for scanning
            now = datetime.now(ZoneInfo(entity.timezone) if entity.timezone else None)
            # Start from yesterday and scan back through the previous week
            scan = now - timedelta(days=1)
            week_cutoff = now - timedelta(days=now.weekday() + 7)  # Previous Monday

            print(f"Scanning entity {entity.name} ({entity.code}) - {entity.id} for missing daily prices from {scan.date()} back to {week_cutoff.date()}")

            open_task_count = 0
            while scan.date() >= week_cutoff.date():
                # don't create more than max open tasks
                if open_task_count >= max_open_tasks:
                    break

                day_start = datetime(scan.year, scan.month, scan.day, tzinfo=scan.tzinfo)
                day_end = day_start + timedelta(days=1)

                # skip weekends if has_weekend is False
                if not entity.has_weekend and day_start.date().weekday() >= 5:
                    scan = scan - timedelta(days=1)
                    continue

                print(f"Scanning day {day_start.date()}")

                # get the list of prices for the day
                prices = self.price_manager_client.query_prices(entity.id, day_start, day_end, page=0, size=31)

                # check if there are missing prices for the day
                if _day_has_missing_prices(prices, day_start, day_end, entity.has_weekend):
                    print(f"Day {day_start.date()} has missing prices, checking for existing task")

                    current_task = self.db.get_task_for_range(entity.id, day_start, day_end)

                    if not current_task:
                        print(f"No existing task for day {day_start.date()}, creating new task")
                        tr = self.db.create_new_task(entity.id, day_start, day_end)
                        created.append(tr)
                        open_task_count += 1
                    else:
                        if current_task.status == "OPEN":
                            print(f"Existing OPEN task {current_task.id} for day {day_start.date()}")
                            open_task_count += 1
                else:
                    print(f"Day {day_start.date()} has no missing prices")

                scan = scan - timedelta(days=1)

        return created

    def generate_weekly_tasks(self, entity_id: Optional[UUID] = None, max_open_tasks: int = 2000) -> Iterable[TaskRecord]:
        """Generate weekly tasks for missing daily prices up to the previous month."""
        created: List[TaskRecord] = []

        entities = self.price_manager_client.query_entities(
            entity_id,
            None,
            None,
            "DAILY")

        for entity in entities:
            # count OPEN tasks for the entity
            open_count = self.db.count_tasks(entity.id, "OPEN")
            if open_count >= max_open_tasks:
                print(f"Entity {entity.name} ({entity.code}) - {entity.id} has {open_count} OPEN tasks, skipping")
                continue

            # get the previous week range as a starting point for scanning
            now = datetime.now(ZoneInfo(entity.timezone) if entity.timezone else None)
            scan = _prev_week(now)

            # determine the stop date for scanning (previous month)
            month_ago = now - timedelta(days=30)
            stop_date = datetime(month_ago.year, month_ago.month, month_ago.day, tzinfo=month_ago.tzinfo).date()

            print(f"Scanning entity {entity.name} ({entity.code}) - {entity.id} for missing weekly prices starting from {scan.date()} back to {stop_date}")

            open_task_count = 0
            while True:
                # don't create more than max open tasks
                if open_task_count >= max_open_tasks:
                    break

                # get date range for the week to scan
                week_start, week_end = _week_range_for(scan)

                print(f"Scanning week {week_start.date()} to {week_end.date()}")

                # stop date is inclusive - quit if the scan window is now outside the stop date
                if week_end.date() < stop_date:
                    break

                # get the list of prices for the week
                prices = self.price_manager_client.query_prices(entity.id, week_start, week_end, page=0, size=31)

                # check if there are missing prices for the week
                if _month_has_missing_prices(prices, week_start, week_end, entity.has_weekend):
                    print(f"Week {week_start.date()} to {week_end.date()} has missing prices, checking for existing task")

                    current_task = self.db.get_task_for_range(entity.id, week_start, week_end)

                    if not current_task:
                        print(f"No existing task for week {week_start.date()} to {week_end.date()}, creating new task")
                        tr = self.db.create_new_task(entity.id, week_start, week_end)
                        created.append(tr)
                        open_task_count += 1
                    else:
                        if current_task.status == "OPEN":
                            print(f"Existing OPEN task {current_task.id} for week {week_start.date()} to {week_end.date()}")
                            open_task_count += 1
                        else:
                            print(f"Existing task {current_task.id} for week {week_start.date()} to {week_end.date()} is not OPEN")
                else:
                    print(f"Week {week_start.date()} to {week_end.date()} has no missing prices")

                scan = _prev_week(scan)

        return created

    def get_tasks(self, status: Optional[str], page: int = 0, size: int = 10) -> Iterable[TaskRecord]:
        return self.db.query_tasks(status, page, size)
