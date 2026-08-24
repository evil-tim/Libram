"""Bounded executor for durable recurring snapshot schedules.

Claiming and state transitions are delegated to :class:`Database`; each claim
is committed before the snapshot service is called, so RPC work never runs in
a database transaction.
"""

from __future__ import annotations

import os
import random
import signal
import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Event, Thread
from uuid import UUID, uuid4

from libram_database.db import Database
from price_management import PriceManagerService


class SnapshotSchedulerExecutor:
    """Run due snapshot states with leases and bounded concurrency."""

    def __init__(
        self,
        price_manager_client: PriceManagerService,
        db: Database,
        *,
        thread_count: int = 2,
        poll_interval_seconds: float = 10,
        lease_seconds: int = 300,
        retry_delay_seconds: int = 30,
        max_backoff_seconds: int = 1800,
        rpc_concurrency: int = 2,
        jitter_seconds: float = 5,
        shutdown_timeout_seconds: float = 30,
        worker_id: str | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
        random_uniform: Callable[[float, float], float] | None = None,
    ) -> None:
        if thread_count <= 0 or lease_seconds <= 0 or poll_interval_seconds < 0:
            raise ValueError("worker and lease settings must be positive")
        if retry_delay_seconds <= 0 or max_backoff_seconds <= 0 or rpc_concurrency <= 0:
            raise ValueError("retry and concurrency settings must be positive")
        self.price_manager_client = price_manager_client
        self.db = db
        self.thread_count = thread_count
        self.poll_interval_seconds = poll_interval_seconds
        self.lease_seconds = lease_seconds
        self.retry_delay_seconds = retry_delay_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.rpc_concurrency = rpc_concurrency
        self.jitter_seconds = max(0.0, jitter_seconds)
        self.shutdown_timeout_seconds = max(0.0, shutdown_timeout_seconds)
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid4()}"
        self.clock = clock or (lambda: datetime.now(UTC))
        self.sleep = sleep or time.sleep
        self.random_uniform = random_uniform or random.uniform
        self.stop_event = Event()
        self._threads: list[Thread] = []
        self._global_limiter = threading.BoundedSemaphore(rpc_concurrency)
        self._provider_limiters: dict[str, threading.BoundedSemaphore] = {}
        self._provider_lock = threading.Lock()

    @classmethod
    def from_environment(
        cls, price_manager_client: PriceManagerService, db: Database, **kwargs
    ) -> SnapshotSchedulerExecutor:
        """Construct an executor using snapshot-specific environment defaults."""

        def number(name: str, default: str, cast):
            return cast(os.environ.get(name, default))

        return cls(
            price_manager_client,
            db,
            thread_count=number("LIBRAM_SNAPSHOT_SCHEDULER_THREADS", "2", int),
            poll_interval_seconds=number(
                "LIBRAM_SNAPSHOT_POLL_INTERVAL_SECONDS", "10", float
            ),
            lease_seconds=number("LIBRAM_SNAPSHOT_LEASE_SECONDS", "300", int),
            retry_delay_seconds=number(
                "LIBRAM_SNAPSHOT_RETRY_DELAY_SECONDS", "30", int
            ),
            max_backoff_seconds=number(
                "LIBRAM_SNAPSHOT_MAX_BACKOFF_SECONDS", "1800", int
            ),
            rpc_concurrency=number("LIBRAM_SNAPSHOT_RPC_CONCURRENCY", "2", int),
            **kwargs,
        )

    def setup_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, self.handle_stop)
        signal.signal(signal.SIGTERM, self.handle_stop)

    def handle_stop(self, signum: int, frame: object) -> None:
        self.stop()

    def start(self) -> None:
        self._threads = [
            Thread(target=self.worker, name=f"snapshot-{i}", daemon=True)
            for i in range(self.thread_count)
        ]
        for thread in self._threads:
            thread.start()
        try:
            while not self.stop_event.wait(1):
                pass
        except KeyboardInterrupt:
            self.stop()
        deadline = time.monotonic() + self.shutdown_timeout_seconds
        for thread in self._threads:
            thread.join(max(0.0, deadline - time.monotonic()))

    def stop(self) -> None:
        self.stop_event.set()

    def worker(self) -> None:
        while not self.stop_event.is_set():
            if not self.execute_once():
                self.stop_event.wait(self.poll_interval_seconds)

    def execute_once(self) -> bool:
        """Claim and execute at most one row; return whether work was claimed."""
        thread_ident = threading.get_ident()
        token = uuid4()
        state = self.db.claim_due_snapshot(token, self.worker_id, self.lease_seconds)
        if state is None:
            return False
        print(f"{datetime.now().isoformat()} : [{thread_ident}] Claimed snapshot job for entity {state.entity_id}")
        started = self.clock()
        try:
            provider_key = self._provider_key(state.entity_id)
            with self._limits(provider_key):
                self.price_manager_client.fetch_snapshot_and_store(state.entity_id)
                print(f"{datetime.now().isoformat()} : [{thread_ident}] Snapshot for {state.entity_id} created")
        except Exception as error:  # noqa: BLE001 - recurring streams must survive failures
            message = f"{type(error).__name__}: {error}"[:2000]
            delay_jitter = int(
                self.random_uniform(0, self.jitter_seconds)
                if self.jitter_seconds
                else 0
            )
            self.db.fail_snapshot(
                state.entity_id,
                token,
                message,
                self.retry_delay_seconds,
                self.max_backoff_seconds,
                delay_jitter,
            )
            print(f"{datetime.now().isoformat()} : [{thread_ident}] Snapshot for {state.entity_id} failed")
            return True
        duration_ms = max(0, int((self.clock() - started).total_seconds() * 1000))
        self.db.complete_snapshot(state.entity_id, token, self.clock(), duration_ms)
        print(f"{datetime.now().isoformat()} : [{thread_ident}] Released snapshot job for entity {state.entity_id}")
        return True

    def _provider_key(self, entity_id: UUID) -> str:
        entity = self.db.get_entity_by_id_raw(entity_id) or {}
        config = entity.get("config")
        datasource_id = entity.get("datasource_id")
        if datasource_id:
            datasource = self.db.get_datasource_raw(UUID(str(datasource_id)))
            if datasource:
                datasource_config = datasource.get("config")
                if isinstance(datasource_config, dict):
                    config = {
                        **datasource_config,
                        **(config if isinstance(config, dict) else {}),
                    }
        if isinstance(config, dict):
            for key in ("rpc_url", "rpcUrl", "provider_key"):
                value = config.get(key)
                if value:
                    return str(value)
        return str(entity.get("datasource_id") or entity_id)

    def _provider_limiter(self, key: str) -> threading.BoundedSemaphore:
        with self._provider_lock:
            return self._provider_limiters.setdefault(
                key, threading.BoundedSemaphore(self.rpc_concurrency)
            )

    @contextmanager
    def _limits(self, provider_key: str) -> Iterator[None]:
        provider = self._provider_limiter(provider_key)
        while not self.stop_event.is_set() and not self._global_limiter.acquire(
            timeout=0.2
        ):
            pass
        if self.stop_event.is_set():
            raise RuntimeError("snapshot scheduler stopping")
        try:
            while not self.stop_event.is_set() and not provider.acquire(timeout=0.2):
                pass
            if self.stop_event.is_set():
                raise RuntimeError("snapshot scheduler stopping")
            try:
                yield
            finally:
                provider.release()
        finally:
            self._global_limiter.release()
