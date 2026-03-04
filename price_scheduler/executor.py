""" The price scheduler module is responsible for scheduling and running tasks to fetch price data for financial entities.
"""

import random
import signal

from threading import Event, Thread
from typing import Optional

from libram_database.db import Database
from price_management import PriceManagerClient


class PriceSchedulerExecutor:
    """Worker-based scheduler that polls for tasks and executes price fetches.

    Notes:
    - Uses a `threading.Event` (`stop_event`) for shutdown signalling so workers
      can be woken immediately.
    - Signal handlers are only registered when running on the main thread.
    """

    def __init__(
        self,
        price_manager_client: PriceManagerClient,
        db: Database,
        max_retries: int = 5,
        retry_delay_seconds: int = 300,
        thread_count: int = 4,
        poll_interval: int = 60,
        jitter: int = 30,
    ):
        self.price_manager_client = price_manager_client
        self.db = db
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds
        self.thread_count = thread_count
        self.poll_interval = poll_interval
        self.jitter = jitter
        self.stop_event = Event()

    """ Signal handlers are only set up if this is the main thread,
    to avoid issues with signal handling in multi-threaded contexts."""
    def setup_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, self.handle_stop)
        signal.signal(signal.SIGTERM, self.handle_stop)

    def handle_stop(self, signum, frame) -> None:
        self.stop()

    def start(self) -> None:
        # Initialize worker threads
        threads = []
        for _ in range(self.thread_count):
            t = Thread(target=self.worker, daemon=True)
            threads.append(t)
        # Start worker threads
        for t in threads:
            t.start()

        try:
            # Wait until stop_event is set; this makes shutdown responsive.
            while not self.stop_event.wait(timeout=5):
                continue
        except KeyboardInterrupt:
            self.stop()

        # join threads with a short timeout to avoid hanging indefinitely
        for t in threads:
            t.join(timeout=5)

    def stop(self) -> None:
        self.stop_event.set()

    def worker(self) -> None:
        initial_run = True
        # Main loop for worker threads: execute tasks until stop_event is set
        while not self.stop_event.is_set():
            if initial_run:
                initial_run = False
                init_delay = random.randrange(1, self.jitter)
                # random wait
                if self.stop_event.wait(timeout=init_delay):
                    break

            # Execute task, don't care about the result
            self.execute_task()

            # Always wait for the poll interval, but wake early if stop_event is set
            if self.stop_event.wait(timeout=self.poll_interval):
                break

    def execute_task(self) -> None:
        # fetch the next task to execute
        task = self.db.find_and_lock_next_task(self.retry_delay_seconds)
        if not task:
            print("No tasks to execute")
            return

        if not task.timestamp_start:
            raise ValueError("task must have timestamp_start")
        if not task.timestamp_end:
            raise ValueError("task must have timestamp_end")

        try:
            # fetch and store prices for the task's entity and time range
            self.price_manager_client.fetch_and_store(
                entity_id=task.entity_id,
                entity_code=None,
                start=task.timestamp_start,
                end=task.timestamp_end,
            )
            self.db.complete_task(task.id)
        except Exception as e:
            # log the error and mark the task as failed if it has exceeded max retries
            print(f"Error executing task {task.id}: {e}")
            self.db.fail_task(task.id, self.max_retries)