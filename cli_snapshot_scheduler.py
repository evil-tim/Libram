"""Run the standalone scheduler for recurring snapshot observations."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

from dotenv import load_dotenv

from libram_database.db import Database
from price_management.service import PriceManagerService
from snapshot_scheduler.executor import SnapshotSchedulerExecutor


def _integer_setting(name: str, default: int) -> int:
    """Read a positive integer setting, with an actionable error message."""
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer, got {value!r}") from error
    if parsed <= 0:
        raise ValueError(f"{name} must be greater than zero, got {parsed}")
    return parsed


def _executor_settings() -> dict[str, int]:
    """Return snapshot-only executor settings from the process environment."""
    return {
        "thread_count": _integer_setting("LIBRAM_SNAPSHOT_SCHEDULER_THREADS", 2),
        "poll_interval_seconds": _integer_setting(
            "LIBRAM_SNAPSHOT_POLL_INTERVAL_SECONDS", 10
        ),
        "lease_seconds": _integer_setting("LIBRAM_SNAPSHOT_LEASE_SECONDS", 300),
        "retry_delay_seconds": _integer_setting(
            "LIBRAM_SNAPSHOT_RETRY_DELAY_SECONDS", 30
        ),
        "max_backoff_seconds": _integer_setting(
            "LIBRAM_SNAPSHOT_MAX_BACKOFF_SECONDS", 1800
        ),
        "rpc_concurrency": _integer_setting("LIBRAM_SNAPSHOT_RPC_CONCURRENCY", 2),
        "shutdown_timeout_seconds": _integer_setting(
            "LIBRAM_SNAPSHOT_SHUTDOWN_TIMEOUT_SECONDS", 360
        ),
    }


def build_executor(
    database_factory: Callable[[str], Database] = Database,
    manager_factory: Callable[[Database], PriceManagerService] = PriceManagerService,
    executor_factory: Callable[
        ..., SnapshotSchedulerExecutor
    ] = SnapshotSchedulerExecutor,
) -> SnapshotSchedulerExecutor:
    """Construct the snapshot executor without starting its long-running loop."""
    db_string = os.getenv("LIBRAM_DB")
    if not db_string:
        raise ValueError("LIBRAM_DB environment variable not set")
    db = database_factory(db_string)
    manager = manager_factory(db)
    return executor_factory(manager, db, **_executor_settings())


def main() -> int:
    load_dotenv()
    try:
        executor = build_executor()
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    executor.setup_signal_handlers()
    executor.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
