from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass
class TaskRecord:
    id: UUID
    entity_id: UUID
    timestamp_start: datetime
    timestamp_end: datetime
    status: str
    retry_count: int
    created_at: datetime
