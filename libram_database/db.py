import json
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from libram_types.libram_types import (
    DividendEventRecord,
    EntityRecord,
    PortfolioDividendRecord,
    PortfolioOrderRecord,
    PortfolioRecord,
    PriceRecord,
    TaskRecord,
)

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


class Database:
    """Small helper around SQLAlchemy for the provided schema.

    Example DSN: postgres://user:pass@host:5432/dbname
    """

    def __init__(self, dsn: str):
        self.dsn = dsn
        self.engine: Engine = create_engine(dsn)

    def init_db(self, schema_path: Path = _SCHEMA_PATH):
        """Create any missing tables and indexes from the idempotent schema.sql.

        All DDL in schema.sql uses IF NOT EXISTS, so
        repeated calls are safe — no migrations or destructive changes.
        """
        ddl = schema_path.read_text(encoding="utf-8")
        with self.engine.begin() as conn:
            conn.execute(text(ddl))

    # datasource methods
    def get_datasource_raw(self, datasource_id: UUID) -> Optional[dict[str, object]]:
        with self.engine.connect() as conn:
            q = text("SELECT * FROM datasource WHERE id = :id")
            res = conn.execute(q, {"id": str(datasource_id)})
            row = res.mappings().first()
            return dict(row) if row else None

    # entity methods
    def get_entity_by_id_raw(self, identifier: UUID) -> Optional[dict[str, object]]:
        """Lookup an entity by UUID.

        Returns a mapping with keys matching the `entity` table columns.
        """
        with self.engine.connect() as conn:
            q = text("SELECT * FROM entity WHERE id = :id")
            res = conn.execute(q, {"id": str(identifier)})
            row = res.mappings().first()
            return dict(row) if row else None

    def get_entity_by_code_raw(self, code: str) -> Optional[dict[str, object]]:
        """Lookup an entity by code.

        Returns a mapping with keys matching the `entity` table columns.
        """
        with self.engine.connect() as conn:
            q = text("SELECT * FROM entity WHERE code = :code")
            res = conn.execute(q, {"code": code})
            row = res.mappings().first()
            return dict(row) if row else None

    def query_entities(self, entity_id: Optional[UUID], entity_code: Optional[str], entity_name: Optional[str], frequency: Optional[str]) -> Iterable[EntityRecord]:
        """Queries entities by code and/or name. Code parameter is exact match. Name parameter supports partial match (LIKE %param%)."""
        q = text(
            """
            SELECT e.* FROM entity e
            WHERE (:entity_id IS NULL OR e.id = :entity_id)
            AND (:code IS NULL OR e.code = :code)
            AND (:name IS NULL OR e.name ILIKE '%' || :name || '%')
            AND (:frequency IS NULL OR e.frequency = :frequency)
            ORDER BY e.type, e.code
            """
        )
        with self.engine.connect() as conn:
            res = conn.execute(q, {"entity_id": entity_id, "code": entity_code, "name": entity_name, "frequency": frequency})
            rows = res.mappings().all()

        out: list[EntityRecord] = []
        for r in rows:
            db_entity_id = r.get("id")
            if not db_entity_id or not isinstance(db_entity_id, UUID):
                raise RuntimeError("entity id is not a UUID")  # should not be possible, id is required UUID
            out.append(
                EntityRecord(
                    id=db_entity_id,
                    code=r.get("code"),
                    name=r.get("name"),
                    currency=r.get("currency_id"),
                    datasource=r.get("datasource_id"),
                    config=r.get("config"),
                    type=r.get("type"),
                    frequency=r.get("frequency"),
                    has_weekend=bool(r.get("has_weekend")) if r.get("has_weekend") else False,
                    timezone=r.get("timezone"),
                    min_timestamp=r.get("min_timestamp"),
                )
            )
        return out

    # price methods
    def save_prices(self, entity_id: UUID, prices: Iterable[PriceRecord]) -> int:
        """Insert price rows. Returns number of rows inserted."""

        exists_sql = text(
            """
            SELECT 1 FROM price
            WHERE entity_id = :entity_id
            AND (
                (timestamp IS NOT NULL AND timestamp = :timestamp)
                OR
                (timestamp_start IS NOT NULL AND timestamp_end IS NOT NULL AND timestamp_start = :timestamp_start AND timestamp_end = :timestamp_end)
            )
            LIMIT 1
            """
        )
        insert_sql = text(
            """
            INSERT INTO price
                (entity_id, price, timestamp, open, high, low, close, timestamp_start, timestamp_end)
            VALUES
                (:entity_id, :price, :timestamp, :open, :high, :low, :close, :timestamp_start, :timestamp_end)
            """
        )

        # Use a single transaction for all existence checks and the final insert.
        rows = []
        with self.engine.begin() as conn:
            for p in prices:
                # skip if price is null to avoid inserting invalid data
                has_single_price = p.price is not None and p.timestamp is not None
                has_ohlc = (p.open is not None and p.high is not None and p.low is not None and p.close is not None
                            and p.timestamp_start is not None and p.timestamp_end is not None)
                if not has_single_price and not has_ohlc:
                    continue

                # skip if timestamp is null or not a valid datetime to avoid inserting invalid data
                # or if timestamp range is invalid (start or end is null or not a valid datetime or start is after end)
                valid_single_timestamp = p.timestamp is not None and isinstance(p.timestamp, datetime)
                valid_timestamp_range = (
                    p.timestamp_start is not None
                    and isinstance(p.timestamp_start, datetime)
                    and p.timestamp_end is not None
                    and isinstance(p.timestamp_end, datetime)
                    and p.timestamp_start <= p.timestamp_end
                )
                if not valid_single_timestamp and not valid_timestamp_range:
                    continue

                # check existence using the same transaction/connection
                res = conn.execute(
                    exists_sql,
                    {
                        "entity_id": str(entity_id),
                        "timestamp": p.timestamp,
                        "timestamp_start": p.timestamp_start,
                        "timestamp_end": p.timestamp_end,
                    },
                )
                if res.first() is not None:
                    continue

                rows.append(
                    {
                        "entity_id": str(entity_id),
                        "price": p.price,
                        "timestamp": p.timestamp,
                        "open": p.open,
                        "high": p.high,
                        "low": p.low,
                        "close": p.close,
                        "timestamp_start": p.timestamp_start,
                        "timestamp_end": p.timestamp_end,
                    }
                )

            if not rows:
                return 0

            conn.execute(insert_sql, rows)

        return len(rows)

    def query_prices(self, entity_id: UUID, start: datetime, end: datetime, page: int = 0, size: int = 10) -> Iterable[PriceRecord]:
        """Query both single-timestamp and interval (OHLC) price rows covering the range.
        Range is inclusive of start and exclusive of end (i.e. [start, end)).
        Returns a list of PriceRecord with timestamps in the requested range.
        """
        q = text(
            """
            SELECT * FROM price
            WHERE entity_id = :entity_id
            AND (
                (timestamp IS NOT NULL AND timestamp >= :start AND timestamp < :end)
                OR
                (timestamp_start IS NOT NULL AND timestamp_end IS NOT NULL AND timestamp_start < :end AND timestamp_end > :start)
            )
            ORDER BY COALESCE(timestamp, timestamp_start)
            LIMIT :limit OFFSET :offset
            """
        )
        with self.engine.connect() as conn:
            res = conn.execute(q, {"entity_id": str(entity_id), "start": start, "end": end, "limit": size, "offset": page * size})
            rows = res.mappings().all()

        out = []
        for r in rows:
            out.append(
                PriceRecord(
                    price=r.get("price"),
                    timestamp=r.get("timestamp"),
                    open=r.get("open"),
                    high=r.get("high"),
                    low=r.get("low"),
                    close=r.get("close"),
                    timestamp_start=r.get("timestamp_start"),
                    timestamp_end=r.get("timestamp_end"),
                )
            )
        return out

    def count_prices(self, entity_id: UUID, start: datetime, end: datetime) -> int:
        """Count price rows covering the range. Range is inclusive of start and exclusive of end (i.e. [start, end))."""
        q = text(
            """
            SELECT COUNT(*) as c FROM price
            WHERE entity_id = :entity_id
            AND (
                    (
                    timestamp IS NOT NULL
                    AND timestamp >= :start
                    AND timestamp < :end
                    )
                OR
                    (
                    timestamp_start IS NOT NULL
                    AND timestamp_end IS NOT NULL
                    AND timestamp_start >= :start
                    AND timestamp_start < :end
                    )
            )
            """
        )
        with self.engine.connect() as conn:
            res = conn.execute(q, {"entity_id": str(entity_id), "start": start, "end": end})
            row = res.mappings().first()
            count = row.get("c") if row else None
            return int(count) if count is not None else 0

    # task methods
    def count_tasks(self, entity_id: UUID, status: str) -> int:
        """Return the number of tasks for an entity with the given status."""
        q = text(
            "SELECT COUNT(*) as c FROM task WHERE entity_id = :entity_id AND status = :status"
        )
        with self.engine.connect() as conn:
            res = conn.execute(q, {"entity_id": str(entity_id), "status": status})
            row = res.mappings().first()
            count = row.get("c") if row else None
            return int(count) if count is not None else 0

    def create_new_task(self, entity_id: UUID, start: datetime, end: datetime) -> TaskRecord:
        """Create a new task row and return a TaskRecord for it."""
        q = text(
            "INSERT INTO task (entity_id, timestamp_start, timestamp_end) VALUES (:entity_id, :start, :end) RETURNING *"
        )
        with self.engine.begin() as conn:
            res = conn.execute(q, {"entity_id": str(entity_id), "start": start, "end": end})
            row = res.mappings().first()
            if not row:
                raise RuntimeError("failed to create task")

        task_id = row.get("id")
        if not task_id or not isinstance(task_id, UUID):
            raise RuntimeError("task id is not a UUID")
        db_entity_id = row.get("entity_id")
        if not db_entity_id or not isinstance(db_entity_id, UUID):
            raise RuntimeError("task entity_id is not a UUID")

        return TaskRecord(
            id=task_id,
            entity_id=db_entity_id,
            timestamp_start=row.get("timestamp_start"),
            timestamp_end=row.get("timestamp_end"),
            status=row.get("status"),
            retry_count=row.get("retry_count"),
            created_at=row.get("created_at"),
            next_run_at=row.get("next_run_at"),
        )

    def get_task_for_range(self, entity_id: UUID, start: datetime, end: datetime) -> Optional[TaskRecord]:
        """Return a single task matching the entity and exact start/end range, or None."""
        q = text(
            "SELECT * FROM task WHERE entity_id = :entity_id AND timestamp_start = :start AND timestamp_end = :end LIMIT 1"
        )
        with self.engine.connect() as conn:
            res = conn.execute(q, {"entity_id": str(entity_id), "start": start, "end": end})
            row = res.mappings().first()
            if not row:
                return None

        task_id = row.get("id")
        if not task_id or not isinstance(task_id, UUID):
            raise RuntimeError("task id is not a UUID")
        db_entity_id = row.get("entity_id")
        if not db_entity_id or not isinstance(db_entity_id, UUID):
            raise RuntimeError("task entity_id is not a UUID")

        return TaskRecord(
            id=task_id,
            entity_id=db_entity_id,
            timestamp_start=row.get("timestamp_start"),
            timestamp_end=row.get("timestamp_end"),
            status=row.get("status"),
            retry_count=row.get("retry_count"),
            created_at=row.get("created_at"),
        )

    def query_tasks(self, status: Optional[str], page: int = 0, size: int = 10) -> Iterable[TaskRecord]:
        """Return a list of tasks optionally filtered by status, ordered by created_at ascending, limited to the specified number."""
        q = text(
            "SELECT * FROM task WHERE (status = :status OR :status IS NULL) ORDER BY created_at ASC LIMIT :limit OFFSET :offset"
        )
        with self.engine.connect() as conn:
            res = conn.execute(q, {"status": status, "limit": size, "offset": page * size})
            rows = res.mappings().all()

        out = []
        for r in rows:

            task_id = r.get("id")
            if not task_id or not isinstance(task_id, UUID):
                raise RuntimeError("task id is not a UUID")
            db_entity_id = r.get("entity_id")
            if not db_entity_id or not isinstance(db_entity_id, UUID):
                raise RuntimeError("task entity_id is not a UUID")
            out.append(
                TaskRecord(
                    id=task_id,
                    entity_id=db_entity_id,
                    timestamp_start=r.get("timestamp_start"),
                    timestamp_end=r.get("timestamp_end"),
                    status=r.get("status"),
                    retry_count=r.get("retry_count"),
                    created_at=r.get("created_at"),
                    next_run_at=r.get("next_run_at"),
                )
            )
        return out

    def find_and_lock_next_task(self, retry_delay_seconds: int, poll_interval: int, max_tasks_per_datasource: int) -> Optional[TaskRecord]:
        """Find the next task that is ready to be executed
        * status should be OPEN
        * next_run_at should be in the past (i.e. task is ready to run)
        * should be for an entity having a datasource where the number of tasks that were
        executed with the poll_interval seconds is less than max_tasks_per_datasource (to avoid overwhelming a datasource
        with too many concurrent tasks)

        If such a task is found, lock it by setting status to IN_PROGRESS and incrementing retry_count, update next_run_at to
        now() + retry_delay_seconds * 3^(pre_increment_retry_count + 1) (exponential backoff with +1 to avoid zero backoff),
        and return the TaskRecord for the locked task.

        Returns the TaskRecord for the locked task, or None if no task is ready.
        """
        q = text(
            """
            WITH next_task AS (
                SELECT id FROM task
                WHERE status = 'OPEN'
                    AND next_run_at <= now()
                    AND (SELECT COUNT(*) FROM task t2
                    JOIN entity e ON t2.entity_id = e.id
                    JOIN datasource d ON e.datasource_id = d.id
                    WHERE t2.updated_at >= now() - (interval '1 second' * :poll_interval)
                    AND d.id = (SELECT datasource_id FROM entity WHERE id = task.entity_id)
                ) < :max_tasks_per_datasource
                ORDER BY created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE task
            SET
                status = 'IN_PROGRESS',
                retry_count = retry_count + 1,
                next_run_at = now() + (interval '1 second' * (:retry_delay_seconds * power(3, retry_count + 1))),
                updated_at = now()
            FROM next_task
            WHERE task.id = next_task.id
            RETURNING task.*
            """
        )
        with self.engine.begin() as conn:
            res = conn.execute(q, {"retry_delay_seconds": retry_delay_seconds, "poll_interval": poll_interval, "max_tasks_per_datasource": max_tasks_per_datasource})
            row = res.mappings().first()
            if not row:
                return None

            task_id = row.get("id")
            if not task_id or not isinstance(task_id, UUID):
                raise RuntimeError("task id is not a UUID")
            db_entity_id = row.get("entity_id")
            if not db_entity_id or not isinstance(db_entity_id, UUID):
                raise RuntimeError("task entity_id is not a UUID")

            return TaskRecord(
                id=task_id,
                entity_id=db_entity_id,
                timestamp_start=row.get("timestamp_start"),
                timestamp_end=row.get("timestamp_end"),
                status=row.get("status"),
                retry_count=row.get("retry_count"),
                created_at=row.get("created_at"),
                next_run_at=row.get("next_run_at"),
            )

    def fail_task(self, task_id: UUID, max_retries: int):
        """ Handle task failure, if retry_count exceeds max_retries, set status to FAILED,
        otherwise set it back to OPEN for retry. Locking the task already incremented the
        retry_count, so we just need to check if it exceeded max_retries."""
        q = text(
            """
            UPDATE task
            SET status = CASE WHEN retry_count >= :max_retries THEN 'FAILED' ELSE 'OPEN' END,
                updated_at = now()
            WHERE id = :task_id
            """
        )
        with self.engine.begin() as conn:
            conn.execute(q, {"task_id": str(task_id), "max_retries": max_retries})

    def complete_task(self, task_id: UUID):
        """Set task status to COMPLETED."""
        q = text(
            """
            UPDATE task
            SET status = 'COMPLETED',
                updated_at = now()
            WHERE id = :task_id
            """
        )
        with self.engine.begin() as conn:
            conn.execute(q, {"task_id": str(task_id)})

    def query_close_series(self, entity_id: UUID, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
        """Fetch the full ordered close/price series for an entity and date range.

        Used by moving-average computations which need the full window (no pagination).
        Uses COALESCE(close, price) to handle both OHLC and single-price entities.
        Returns a list of (timestamp, value) tuples ordered by timestamp ascending.
        Rows where both close and price are NULL are skipped.
        """
        q = text(
            """
            SELECT COALESCE(timestamp, timestamp_start) AS ts,
                   COALESCE(close, price) AS value
            FROM price
            WHERE entity_id = :entity_id
            AND (
                (timestamp IS NOT NULL AND timestamp >= :start AND timestamp < :end)
                OR
                (timestamp_start IS NOT NULL AND timestamp_end IS NOT NULL AND timestamp_start < :end AND timestamp_end > :start)
            )
            AND COALESCE(close, price) IS NOT NULL
            ORDER BY COALESCE(timestamp, timestamp_start)
            """
        )
        with self.engine.connect() as conn:
            res = conn.execute(q, {"entity_id": str(entity_id), "start": start, "end": end})
            rows = res.all()
        out: list[tuple[datetime, float]] = []
        for r in rows:
            ts = r[0]
            value = r[1]
            if ts is None or value is None:
                continue
            out.append((ts, float(value)))
        return out

    def query_price_summary(self, entity_id: UUID, start: datetime, end: datetime) -> Optional[dict[str, object]]:
        """Query aggregate summary statistics for a price series in a date range.

        Uses COALESCE(close, price) to handle both OHLC and single-price entities.
        All aggregation (COUNT, MIN, MAX, AVG, STDDEV_POP) runs in PostgreSQL.
        first_close / last_close are fetched via windowed subqueries.
        period_return_pct is computed in SQL as ((last - first) / first) * 100.

        Returns a dict with keys: count, min, max, avg, std_dev,
        first_close, last_close, period_return_pct — or None if no rows found.
        """
        q = text(
            """
            WITH price_series AS (
                SELECT COALESCE(close, price) AS p,
                       ROW_NUMBER() OVER (ORDER BY COALESCE(timestamp, timestamp_start)) AS rn,
                       COUNT(*) OVER () AS total
                FROM price
                WHERE entity_id = :entity_id
                  AND (
                    (timestamp IS NOT NULL AND timestamp >= :start AND timestamp < :end)
                    OR
                    (timestamp_start IS NOT NULL AND timestamp_end IS NOT NULL
                     AND timestamp_start < :end AND timestamp_end > :start)
                  )
            ),
            stats AS (
                SELECT
                    COUNT(*)        AS count,
                    MIN(p)          AS min,
                    MAX(p)          AS max,
                    AVG(p)          AS avg,
                    STDDEV_POP(p)   AS std_dev,
                    MIN(CASE WHEN rn = 1 THEN p END)       AS first_close,
                    MIN(CASE WHEN rn = total THEN p END)    AS last_close
                FROM price_series
            )
            SELECT
                s.count,
                s.min,
                s.max,
                s.avg,
                COALESCE(s.std_dev, 0)          AS std_dev,
                s.first_close,
                s.last_close,
                CASE
                    WHEN s.first_close IS NOT NULL AND s.first_close <> 0
                    THEN ROUND(((s.last_close - s.first_close) / s.first_close) * 100, 2)
                    ELSE 0
                END                             AS period_return_pct
            FROM stats s
            """
        )
        with self.engine.connect() as conn:
            res = conn.execute(q, {"entity_id": str(entity_id), "start": start, "end": end})
            row = res.mappings().first()
            if not row:
                return None
            count = row.get("count")
            if not count or int(count) == 0:
                return None
            return {
                "count": int(row["count"]),
                "min": float(row["min"]),
                "max": float(row["max"]),
                "avg": float(row["avg"]),
                "std_dev": float(row["std_dev"]),
                "first_close": float(row["first_close"]),
                "last_close": float(row["last_close"]),
                "period_return_pct": float(row["period_return_pct"]),
            }

    # entity_fundamentals methods
    def insert_fundamentals(
        self,
        entity_id: UUID,
        metrics: dict,
        source_name: str,
        source_url: str,
        as_of_date: datetime,
        confidence: str,
        notes: str,
        uploaded_by: str,
    ) -> dict:
        """Insert a fundamentals snapshot. Returns the inserted row as a dict."""
        q = text(
            """
            INSERT INTO entity_fundamentals
                (entity_id, metrics, source_name, source_url, as_of_date, confidence, notes, uploaded_by)
            VALUES
                (:entity_id, :metrics, :source_name, :source_url, :as_of_date, :confidence, :notes, :uploaded_by)
            RETURNING *
            """
        )
        with self.engine.begin() as conn:
            res = conn.execute(q, {
                "entity_id": str(entity_id),
                "metrics": json.dumps(metrics),
                "source_name": source_name,
                "source_url": source_url,
                "as_of_date": as_of_date,
                "confidence": confidence,
                "notes": notes,
                "uploaded_by": uploaded_by,
            })
            row = res.mappings().first()
            if not row:
                raise RuntimeError("failed to insert fundamentals")
            return dict(row)

    def get_fundamentals_by_entity(
        self,
        entity_id: UUID,
        mode: str = "latest_only",
        min_confidence: str = "low",
        as_of_date_after: Optional[str] = None,
    ) -> list[dict]:
        """Query fundamentals snapshots for an entity, ordered by uploaded_at DESC.

        mode:
          - "latest_only": return the single most recent snapshot (LIMIT 1)
          - "all": return all snapshots
          - "latest_consolidated": return all snapshots (consolidation in Python)

        min_confidence filters snapshots to those at or above the given level:
          - "high": only high-confidence snapshots
          - "medium": high or medium
          - "low": no filter (all pass)

        as_of_date_after (ISO date string, e.g. "2026-01-01") filters snapshots
        to those with as_of_date >= the given date.
        """
        params: dict = {"entity_id": str(entity_id)}
        clauses = ["entity_id = :entity_id"]

        if min_confidence == "high":
            clauses.append("confidence = 'high'")
        elif min_confidence == "medium":
            clauses.append("confidence IN ('high', 'medium')")

        if as_of_date_after is not None:
            clauses.append("as_of_date >= :as_of_date_after")
            params["as_of_date_after"] = as_of_date_after

        where_clause = " AND ".join(clauses)
        limit_clause = "LIMIT 1" if mode == "latest_only" else ""

        q = text(
            f"SELECT * FROM entity_fundamentals "
            f"WHERE {where_clause} "
            f"ORDER BY uploaded_at DESC "
            f"{limit_clause}".strip()
        )

        with self.engine.connect() as conn:
            res = conn.execute(q, params)
            rows = res.mappings().all()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # portfolio methods
    # ------------------------------------------------------------------
    @staticmethod
    def _row_to_portfolio(row) -> PortfolioRecord:
        portfolio_id = row.get("id")
        if not portfolio_id or not isinstance(portfolio_id, UUID):
            raise RuntimeError("portfolio id is not a UUID")
        return PortfolioRecord(
            id=portfolio_id,
            name=row.get("name"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    @staticmethod
    def _row_to_order(row) -> PortfolioOrderRecord:
        order_id = row.get("id")
        if not order_id or not isinstance(order_id, UUID):
            raise RuntimeError("order id is not a UUID")
        portfolio_id = row.get("portfolio_id")
        if not portfolio_id or not isinstance(portfolio_id, UUID):
            raise RuntimeError("order portfolio_id is not a UUID")
        entity_id = row.get("entity_id")
        if not entity_id or not isinstance(entity_id, UUID):
            raise RuntimeError("order entity_id is not a UUID")
        return PortfolioOrderRecord(
            id=order_id,
            portfolio_id=portfolio_id,
            entity_id=entity_id,
            date=row.get("date"),
            shares=row.get("shares"),
            type=row.get("type"),
            cost_basis=row.get("cost_basis"),
            cost_basis_entity_id=row.get("cost_basis_entity_id"),
            fees=row.get("fees"),
            fees_entity_id=row.get("fees_entity_id"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    @staticmethod
    def _row_to_dividend_event(row) -> DividendEventRecord:
        event_id = row.get("id")
        entity_id = row.get("entity_id")
        if not isinstance(event_id, UUID):
            raise RuntimeError("dividend event id is not a UUID")
        if not isinstance(entity_id, UUID):
            raise RuntimeError("dividend event entity_id is not a UUID")
        return DividendEventRecord(
            id=event_id, entity_id=entity_id, ex_date=row.get("ex_date"),
            declaration_date=row.get("declaration_date"), record_date=row.get("record_date"),
            payment_date=row.get("payment_date"), dividend_type=row.get("dividend_type"),
            amount_per_share=row.get("amount_per_share"),
            amount_per_share_entity_id=row.get("amount_per_share_entity_id"),
            created_at=row.get("created_at"), updated_at=row.get("updated_at"),
        )

    @staticmethod
    def _row_to_portfolio_dividend(row) -> PortfolioDividendRecord:
        for field in ("id", "portfolio_id", "dividend_event_id"):
            if not isinstance(row.get(field), UUID):
                raise RuntimeError(f"portfolio dividend {field} is not a UUID")
        return PortfolioDividendRecord(
            id=row.get("id"), portfolio_id=row.get("portfolio_id"),
            dividend_event_id=row.get("dividend_event_id"), fees=row.get("fees"),
            fees_entity_id=row.get("fees_entity_id"), created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    def create_portfolio(self, name: str) -> PortfolioRecord:
        """Insert a portfolio row and return the record."""
        q = text(
            """
            INSERT INTO portfolio (name)
            VALUES (:name)
            RETURNING *
            """
        )
        with self.engine.begin() as conn:
            res = conn.execute(q, {"name": name})
            row = res.mappings().first()
            if not row:
                raise RuntimeError("failed to create portfolio")
        return self._row_to_portfolio(row)

    def list_portfolios(self) -> list[PortfolioRecord]:
        """Return all portfolios ordered by created_at ascending."""
        q = text("SELECT * FROM portfolio ORDER BY created_at ASC")
        with self.engine.connect() as conn:
            res = conn.execute(q)
            rows = res.mappings().all()
        return [self._row_to_portfolio(r) for r in rows]

    def get_portfolio(self, portfolio_id: UUID) -> Optional[PortfolioRecord]:
        """Lookup a portfolio by UUID."""
        q = text("SELECT * FROM portfolio WHERE id = :id")
        with self.engine.connect() as conn:
            res = conn.execute(q, {"id": str(portfolio_id)})
            row = res.mappings().first()
            if not row:
                return None
        return self._row_to_portfolio(row)

    def update_portfolio(self, portfolio_id: UUID, name: str) -> Optional[PortfolioRecord]:
        """Update a portfolio's name. Returns the updated record or None if not found."""
        q = text(
            """
            UPDATE portfolio
            SET name = :name, updated_at = now()
            WHERE id = :id
            RETURNING *
            """
        )
        with self.engine.begin() as conn:
            res = conn.execute(q, {"id": str(portfolio_id), "name": name})
            row = res.mappings().first()
            if not row:
                return None
        return self._row_to_portfolio(row)

    def delete_portfolio(self, portfolio_id: UUID) -> bool:
        """Delete a portfolio by UUID. Returns True if a row was deleted."""
        q = text("DELETE FROM portfolio WHERE id = :id")
        with self.engine.begin() as conn:
            res = conn.execute(q, {"id": str(portfolio_id)})
            return res.rowcount > 0

    def create_dividend_event(self, *, entity_id: UUID, declaration_date=None, ex_date=None,
                              record_date=None, payment_date=None, dividend_type=None,
                              amount_per_share=None, amount_per_share_entity_id=None):
        columns = ["entity_id", "declaration_date", "ex_date", "record_date", "payment_date", "dividend_type", "amount_per_share", "amount_per_share_entity_id"]
        q = text(f"INSERT INTO dividend_event ({', '.join(columns)}) VALUES ({', '.join(':'+c for c in columns)}) RETURNING *")
        params = {"entity_id": str(entity_id), "declaration_date": declaration_date, "ex_date": ex_date,
                  "record_date": record_date, "payment_date": payment_date, "dividend_type": dividend_type,
                  "amount_per_share": amount_per_share,
                  "amount_per_share_entity_id": str(amount_per_share_entity_id) if amount_per_share_entity_id else None}
        with self.engine.begin() as conn: row = conn.execute(q, params).mappings().first()
        if not row: raise RuntimeError("failed to create dividend event")
        return self._row_to_dividend_event(row)

    def get_dividend_event(self, event_id):
        with self.engine.connect() as conn: row = conn.execute(text("SELECT * FROM dividend_event WHERE id=:id"), {"id": str(event_id)}).mappings().first()
        return self._row_to_dividend_event(row) if row else None

    def list_dividend_events(self, entity_id=None, ex_date_from=None, ex_date_to=None):
        clauses = ["1=1"]; params = {}
        if entity_id: clauses.append("entity_id=:entity_id"); params["entity_id"] = str(entity_id)
        if ex_date_from: clauses.append("ex_date>=:ex_date_from"); params["ex_date_from"] = ex_date_from
        if ex_date_to: clauses.append("ex_date<=:ex_date_to"); params["ex_date_to"] = ex_date_to
        with self.engine.connect() as conn: rows = conn.execute(text(f"SELECT * FROM dividend_event WHERE {' AND '.join(clauses)} ORDER BY ex_date ASC, id ASC"), params).mappings().all()
        return [self._row_to_dividend_event(r) for r in rows]

    def update_dividend_event(self, event_id, **values):
        values = {k:v for k,v in values.items() if k in {"entity_id", "declaration_date", "ex_date", "record_date", "payment_date", "dividend_type", "amount_per_share", "amount_per_share_entity_id"}}
        if not values: return self.get_dividend_event(event_id)
        params = {k:(str(v) if isinstance(v, UUID) else v) for k,v in values.items()}; params["id"] = str(event_id)
        with self.engine.begin() as conn: row = conn.execute(text(f"UPDATE dividend_event SET {', '.join(f'{k}=:{k}' for k in values)}, updated_at=now() WHERE id=:id RETURNING *"), params).mappings().first()
        return self._row_to_dividend_event(row) if row else None

    def delete_dividend_event(self, event_id):
        with self.engine.begin() as conn: return conn.execute(text("DELETE FROM dividend_event WHERE id=:id"), {"id": str(event_id)}).rowcount > 0

    def create_portfolio_dividend(self, *, portfolio_id: UUID, dividend_event_id: UUID, fees: Decimal = Decimal("0"), fees_entity_id=None):
        q = text("INSERT INTO portfolio_dividend (portfolio_id, dividend_event_id, fees, fees_entity_id) VALUES (:portfolio_id,:dividend_event_id,:fees,:fees_entity_id) RETURNING *")
        params = {"portfolio_id": str(portfolio_id), "dividend_event_id": str(dividend_event_id), "fees": fees,
                  "fees_entity_id": str(fees_entity_id) if fees_entity_id else None}
        with self.engine.begin() as conn: row = conn.execute(q, params).mappings().first()
        if not row: raise RuntimeError("failed to create portfolio dividend")
        return self._row_to_portfolio_dividend(row)

    def get_portfolio_dividend(self, portfolio_id, dividend_event_id):
        q=text("SELECT * FROM portfolio_dividend WHERE portfolio_id=:portfolio_id AND dividend_event_id=:dividend_event_id")
        with self.engine.connect() as conn: row=conn.execute(q,{"portfolio_id":str(portfolio_id),"dividend_event_id":str(dividend_event_id)}).mappings().first()
        return self._row_to_portfolio_dividend(row) if row else None

    def update_portfolio_dividend(self, portfolio_id, dividend_event_id, **values):
        values={k:v for k,v in values.items() if k in {"fees","fees_entity_id"}}
        if not values: return self.get_portfolio_dividend(portfolio_id, dividend_event_id)
        params={k:(str(v) if isinstance(v,UUID) else v) for k,v in values.items()}; params.update({"portfolio_id":str(portfolio_id),"dividend_event_id":str(dividend_event_id)})
        q=text(f"UPDATE portfolio_dividend SET {', '.join(f'{k}=:{k}' for k in values)}, updated_at=now() WHERE portfolio_id=:portfolio_id AND dividend_event_id=:dividend_event_id RETURNING *")
        with self.engine.begin() as conn: row=conn.execute(q,params).mappings().first()
        return self._row_to_portfolio_dividend(row) if row else None

    def delete_portfolio_dividend(self, portfolio_id, dividend_event_id):
        with self.engine.begin() as conn: return conn.execute(text("DELETE FROM portfolio_dividend WHERE portfolio_id=:portfolio_id AND dividend_event_id=:dividend_event_id"), {"portfolio_id":str(portfolio_id),"dividend_event_id":str(dividend_event_id)}).rowcount > 0

    def list_portfolio_dividends_for_portfolio(self, portfolio_id):
        with self.engine.connect() as conn: rows=conn.execute(text("SELECT * FROM portfolio_dividend WHERE portfolio_id=:id ORDER BY created_at ASC, id ASC"),{"id":str(portfolio_id)}).mappings().all()
        return [self._row_to_portfolio_dividend(r) for r in rows]

    # ------------------------------------------------------------------
    # portfolio_order methods
    # ------------------------------------------------------------------
    def create_order(
        self,
        portfolio_id: UUID,
        entity_id: UUID,
        date: datetime,
        shares,
        type: str,
        cost_basis,
        cost_basis_entity_id: Optional[UUID],
        fees,
        fees_entity_id: Optional[UUID],
    ) -> PortfolioOrderRecord:
        """Insert a portfolio_order row and return the record."""
        q = text(
            """
            INSERT INTO portfolio_order
                (portfolio_id, entity_id, date, shares, type,
                 cost_basis, cost_basis_entity_id, fees, fees_entity_id)
            VALUES
                (:portfolio_id, :entity_id, :date, :shares, :type,
                 :cost_basis, :cost_basis_entity_id, :fees, :fees_entity_id)
            RETURNING *
            """
        )
        params = {
            "portfolio_id": str(portfolio_id),
            "entity_id": str(entity_id),
            "date": date,
            "shares": shares,
            "type": type,
            "cost_basis": cost_basis,
            "cost_basis_entity_id": str(cost_basis_entity_id) if cost_basis_entity_id else None,
            "fees": fees,
            "fees_entity_id": str(fees_entity_id) if fees_entity_id else None,
        }
        with self.engine.begin() as conn:
            res = conn.execute(q, params)
            row = res.mappings().first()
            if not row:
                raise RuntimeError("failed to create order")
        return self._row_to_order(row)

    def get_order(self, order_id: UUID) -> Optional[PortfolioOrderRecord]:
        """Lookup an order by UUID (any portfolio)."""
        q = text("SELECT * FROM portfolio_order WHERE id = :id")
        with self.engine.connect() as conn:
            res = conn.execute(q, {"id": str(order_id)})
            row = res.mappings().first()
            if not row:
                return None
        return self._row_to_order(row)

    def get_order_for_portfolio(self, order_id: UUID, portfolio_id: UUID) -> Optional[PortfolioOrderRecord]:
        """Lookup an order scoped to a specific portfolio."""
        q = text(
            "SELECT * FROM portfolio_order WHERE id = :id AND portfolio_id = :portfolio_id"
        )
        with self.engine.connect() as conn:
            res = conn.execute(q, {"id": str(order_id), "portfolio_id": str(portfolio_id)})
            row = res.mappings().first()
            if not row:
                return None
        return self._row_to_order(row)

    _ORDER_UPDATEABLE_COLUMNS = {
        "entity_id",
        "date",
        "shares",
        "type",
        "cost_basis",
        "cost_basis_entity_id",
        "fees",
        "fees_entity_id",
    }

    def update_order(self, order_id: UUID, **kwargs) -> Optional[PortfolioOrderRecord]:
        """Update an order with the given fields. Unknown keys are ignored.

        Returns the updated record or None if the order was not found.
        """
        updates = {
            k: v for k, v in kwargs.items()
            if k in self._ORDER_UPDATEABLE_COLUMNS and v is not None
        }
        if not updates:
            # nothing to update; just return the current record if it exists
            return self.get_order(order_id)

        # Normalize UUID values to str for the parameter binding.
        normalized = {}
        for k, v in updates.items():
            if isinstance(v, UUID):
                normalized[k] = str(v)
            else:
                normalized[k] = v
        normalized["id"] = str(order_id)

        set_clauses = [f"{k} = :{k}" for k in updates.keys()]
        set_clauses.append("updated_at = now()")
        set_sql = ", ".join(set_clauses)

        q = text(f"UPDATE portfolio_order SET {set_sql} WHERE id = :id RETURNING *")
        with self.engine.begin() as conn:
            res = conn.execute(q, normalized)
            row = res.mappings().first()
            if not row:
                return None
        return self._row_to_order(row)

    def delete_order(self, order_id: UUID) -> bool:
        """Delete an order by UUID. Returns True if a row was deleted."""
        q = text("DELETE FROM portfolio_order WHERE id = :id")
        with self.engine.begin() as conn:
            res = conn.execute(q, {"id": str(order_id)})
            return res.rowcount > 0

    _ORDER_SORT_COLUMNS = {"date": "po.date", "entity_code": "e.code",
                           "shares": "po.shares", "cost_basis": "po.cost_basis"}

    def query_orders(
        self,
        portfolio_id: UUID,
        page: int = 0,
        size: int = 20,
        entity_id: Optional[UUID] = None,
        order_type: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        sort_by: str = "date",
        sort_order: str = "desc",
    ) -> list[PortfolioOrderRecord]:
        """Query orders for a portfolio with filtering, sorting, and pagination."""
        clauses = ["po.portfolio_id = :portfolio_id"]
        params: dict = {"portfolio_id": str(portfolio_id)}

        if entity_id is not None:
            clauses.append("po.entity_id = :entity_id")
            params["entity_id"] = str(entity_id)
        if order_type is not None:
            clauses.append("po.type = :order_type")
            params["order_type"] = order_type
        if date_from is not None:
            clauses.append("po.date >= :date_from")
            params["date_from"] = date_from
        if date_to is not None:
            clauses.append("po.date <= :date_to")
            params["date_to"] = date_to

        sort_column = self._ORDER_SORT_COLUMNS.get(sort_by, "po.date")
        sort_dir = "ASC" if str(sort_order).lower() == "asc" else "DESC"

        where_sql = " AND ".join(clauses)
        q = text(
            f"""
            SELECT po.*
            FROM portfolio_order po
            LEFT JOIN entity e ON po.entity_id = e.id
            WHERE {where_sql}
            ORDER BY {sort_column} {sort_dir}
            LIMIT :limit OFFSET :offset
            """
        )
        params["limit"] = size
        params["offset"] = page * size

        with self.engine.connect() as conn:
            res = conn.execute(q, params)
            rows = res.mappings().all()
        return [self._row_to_order(r) for r in rows]

    def count_orders(
        self,
        portfolio_id: UUID,
        entity_id: Optional[UUID] = None,
        order_type: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> int:
        """Count orders for a portfolio matching the given filters."""
        clauses = ["portfolio_id = :portfolio_id"]
        params: dict = {"portfolio_id": str(portfolio_id)}

        if entity_id is not None:
            clauses.append("entity_id = :entity_id")
            params["entity_id"] = str(entity_id)
        if order_type is not None:
            clauses.append("type = :order_type")
            params["order_type"] = order_type
        if date_from is not None:
            clauses.append("date >= :date_from")
            params["date_from"] = date_from
        if date_to is not None:
            clauses.append("date <= :date_to")
            params["date_to"] = date_to

        where_sql = " AND ".join(clauses)
        q = text(f"SELECT COUNT(*) AS c FROM portfolio_order WHERE {where_sql}")
        with self.engine.connect() as conn:
            res = conn.execute(q, params)
            row = res.mappings().first()
            count = row.get("c") if row else None
            return int(count) if count is not None else 0

    def get_orders_for_entity(self, portfolio_id: UUID, entity_id: UUID) -> list[PortfolioOrderRecord]:
        """Return all orders for an entity in a portfolio, ordered by date ASC."""
        q = text(
            """
            SELECT * FROM portfolio_order
            WHERE portfolio_id = :portfolio_id AND entity_id = :entity_id
            ORDER BY date ASC
            """
        )
        with self.engine.connect() as conn:
            res = conn.execute(q, {"portfolio_id": str(portfolio_id), "entity_id": str(entity_id)})
            rows = res.mappings().all()
        return [self._row_to_order(r) for r in rows]

    def get_all_orders(self, portfolio_id: UUID) -> list[PortfolioOrderRecord]:
        """Return all orders in a portfolio, ordered by date ASC."""
        q = text(
            "SELECT * FROM portfolio_order WHERE portfolio_id = :portfolio_id ORDER BY date ASC"
        )
        with self.engine.connect() as conn:
            res = conn.execute(q, {"portfolio_id": str(portfolio_id)})
            rows = res.mappings().all()
        return [self._row_to_order(r) for r in rows]

    def get_orders_for_portfolio(self, portfolio_id: UUID) -> list[PortfolioOrderRecord]:
        """Alias for get_all_orders. Kept for naming clarity from the client."""
        return self.get_all_orders(portfolio_id)

    def get_all_orders_across_portfolios(self) -> list[PortfolioOrderRecord]:
        """Return all orders across all portfolios, ordered by date ASC."""
        q = text("SELECT * FROM portfolio_order ORDER BY date ASC")
        with self.engine.connect() as conn:
            res = conn.execute(q)
            rows = res.mappings().all()
        return [self._row_to_order(r) for r in rows]

    # ------------------------------------------------------------------
    # price helpers used by portfolio totals
    # ------------------------------------------------------------------
    def get_latest_price(self, entity_id: UUID):
        """Return the most recent COALESCE(close, price) value for an entity, or None."""
        q = text(
            """
            SELECT COALESCE(close, price) AS value
            FROM price
            WHERE entity_id = :entity_id AND COALESCE(close, price) IS NOT NULL
            ORDER BY COALESCE(timestamp, timestamp_start) DESC
            LIMIT 1
            """
        )
        with self.engine.connect() as conn:
            res = conn.execute(q, {"entity_id": str(entity_id)})
            row = res.first()
            if not row or row[0] is None:
                return None
            return row[0]

    def get_price_at_or_before(self, entity_id: UUID, target: datetime) -> Decimal | None:
        """Return the most recent COALESCE(close, price) value for an entity
        at or before `target`, or None if no price row exists."""
        q = text(
            """
            SELECT COALESCE(close, price) AS value
            FROM price
            WHERE entity_id = :entity_id
              AND COALESCE(close, price) IS NOT NULL
              AND COALESCE(timestamp, timestamp_start) <= :target
            ORDER BY COALESCE(timestamp, timestamp_start) DESC
            LIMIT 1
            """
        )
        with self.engine.connect() as conn:
            res = conn.execute(q, {"entity_id": str(entity_id), "target": target})
            row = res.first()
            if not row or row[0] is None:
                return None
            return row[0]
