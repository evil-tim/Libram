"""Run PriceSchedulerExecutor to process tasks"""

import os

from dotenv import load_dotenv

from libram_database.db import Database
from price_management.client import PriceManagerClient
from price_scheduler.executor import PriceSchedulerExecutor


def main():
    load_dotenv()
    db_string = os.getenv("LIBRAM_DB")
    if not db_string:
        print("Error: LIBRAM_DB environment variable not set")
        return

    args = dict()
    max_retries = os.getenv("LIBRAM_SCHEDULER_MAX_RETRIES")
    retry_delay_seconds = os.getenv("LIBRAM_SCHEDULER_RETRY_DELAY_SECONDS")
    thread_count = os.getenv("LIBRAM_SCHEDULER_THREADS")
    max_tasks_per_datasource = os.getenv("LIBRAM_SCHEDULER_MAX_TASKS_PER_DATASOURCE")
    poll_interval = os.getenv("LIBRAM_SCHEDULER_POLL_INTERVAL_SECONDS")
    jitter = os.getenv("LIBRAM_SCHEDULER_POLL_JITTER_SECONDS")

    if max_retries:
        args["max_retries"] = int(max_retries)
    if retry_delay_seconds:
        args["retry_delay_seconds"] = int(retry_delay_seconds)
    if thread_count:
        args["thread_count"] = int(thread_count)
    if max_tasks_per_datasource:
        args["max_tasks_per_datasource"] = int(max_tasks_per_datasource)
    if poll_interval:
        args["poll_interval"] = int(poll_interval)
    if jitter:
        args["jitter"] = int(jitter)

    db = Database(db_string)
    price_manager_client = PriceManagerClient(db)
    price_scheduler_executor = PriceSchedulerExecutor(price_manager_client, db, **args)

    price_scheduler_executor.setup_signal_handlers()
    price_scheduler_executor.start()


if __name__ == "__main__":
    main()
