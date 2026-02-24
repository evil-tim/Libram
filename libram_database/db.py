from datetime import datetime
from typing import Iterable, List, Optional
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from libram_types.libram_types import EntityRecord, PriceRecord, TaskRecord


class Database:
    """Small helper around SQLAlchemy for the provided schema.

    Example DSN: postgres://user:pass@host:5432/dbname
    """

    def __init__(self, dsn: str):
        self.dsn = dsn
        self.engine: Engine = create_engine(dsn)

    def get_entity_by_id(self, identifier: UUID) -> Optional[dict[str, object]]:
        """Lookup an entity by UUID.

        Returns a mapping with keys matching the `entity` table columns.
        """
        with self.engine.connect() as conn:
            q = text("SELECT * FROM entity WHERE id = :id")
            res = conn.execute(q, {"id": str(identifier)})
            row = res.mappings().first()
            return dict(row) if row else None

    def get_entity_by_code(self, code: str) -> Optional[dict[str, object]]:
        """Lookup an entity by code.

        Returns a mapping with keys matching the `entity` table columns.
        """
        with self.engine.connect() as conn:
            q = text("SELECT * FROM entity WHERE code = :code")
            res = conn.execute(q, {"code": code})
            row = res.mappings().first()
            return dict(row) if row else None

    def get_datasource(self, datasource_id) -> Optional[dict[str, object]]:
        with self.engine.connect() as conn:
            q = text("SELECT * FROM datasource WHERE id = :id")
            res = conn.execute(q, {"id": str(datasource_id)})
            row = res.mappings().first()
            return dict(row) if row else None

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
                if p.price is None:
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

    def query_entities(self, entity_id: Optional[UUID], entity_code: Optional[str], entity_name: Optional[str], frequency: Optional[str]) -> Iterable[EntityRecord]:
        """Queries entities by code and/or name. Code parameter is exact match. Name parameter supports partial match (LIKE %param%)."""
        q = text(
            """
            SELECT e.* FROM entity e
            WHERE (:entity_id IS NULL OR e.id = :entity_id)
            AND (:code IS NULL OR e.code = :code)
            AND (:name IS NULL OR e.name ILIKE '%' || :name || '%')
            AND (:frequency IS NULL OR e.frequency = :frequency)
            """
        )
        with self.engine.connect() as conn:
            res = conn.execute(q, {"entity_id": entity_id, "code": entity_code, "name": entity_name, "frequency": frequency})
            rows = res.mappings().all()

        out: List[EntityRecord] = []
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

    def get_open_tasks(self, limit: int) -> Iterable[TaskRecord]:
        """Return a list of OPEN tasks ordered by created_at ascending, limited to the specified number."""
        q = text(
            "SELECT * FROM task WHERE status = 'OPEN' ORDER BY created_at ASC LIMIT :limit"
        )
        with self.engine.connect() as conn:
            res = conn.execute(q, {"limit": limit})
            rows = res.mappings().all()

        out: List[TaskRecord] = []
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
                )
            )
        return out

    def fetch_prices(self, entity_id: UUID, start: datetime, end: datetime) -> List[PriceRecord]:
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
                (timestamp_start IS NOT NULL AND timestamp_end IS NOT NULL AND timestamp_start < :end AND timestamp_end >= :start)
            )
            ORDER BY COALESCE(timestamp, timestamp_start)
            """
        )
        with self.engine.connect() as conn:
            res = conn.execute(q, {"entity_id": str(entity_id), "start": start, "end": end})
            rows = res.mappings().all()

        out: List[PriceRecord] = []
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
