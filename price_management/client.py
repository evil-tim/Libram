import importlib
from datetime import datetime, timedelta
from typing import Iterable, Optional
from uuid import UUID

from libram_database.db import Database
from libram_types.libram_types import EntityRecord, PriceRecord

from .datasource import BaseDatasource


def _load_datasource(implementation: str, config: dict) -> BaseDatasource:
    """Dynamically load a datasource implementation.

    `implementation` may be either "module:Class" or a module path. If a class
    name is not provided, the loader will look for a `Datasource` attribute
    in the module.
    """
    if ":" in implementation:
        module_name, class_name = implementation.split(":", 1)
    else:
        module_name, class_name = implementation, "Datasource"

    mod = importlib.import_module(module_name)
    cls = getattr(mod, class_name)
    inst = cls(config)
    if not isinstance(inst, BaseDatasource):
        raise TypeError("Datasource implementation must subclass BaseDatasource")
    return inst

class PriceManagerClient:
    """High-level client to fetch/store/query financial data.

    The constructor accepts either a `Database` instance or a DSN string
    (e.g. "postgresql://user:pass@host:5432/dbname"). If a DSN string is
    provided, a `Database` will be constructed internally.
    """

    def __init__(self, db: Database):
        self.db = db

    def fetch_and_store(self, entity_id: Optional[UUID], entity_code: Optional[str], start: datetime, end: datetime) -> int:
        entity = None
        # try to lookup entity by id first
        if entity_id:
            entity = self.db.get_entity_by_id_raw(entity_id)
        # try to lookup entity by code if not found by id
        if entity_code and not entity:
            entity = self.db.get_entity_by_code_raw(entity_code)

        # if entity is still not found, raise an error
        if not entity:
            raise ValueError("entity not found")

        # ensure entity id is a UUID instance
        db_entity_id = entity["id"]
        if not isinstance(db_entity_id, UUID):
            db_entity_id = UUID(str(db_entity_id))

        # if prices already exist for entity and date range, skip fetch and return 0
        if self._prices_exist(entity, start, end):
            return 0

        # if entity's datasource_id is not set or is not a UUID, raise an error
        datasource_id = entity.get("datasource_id")
        if not datasource_id:
            raise ValueError("entity has no datasource_id")
        if not isinstance(datasource_id, UUID):
            raise ValueError("entity's datasource_id is not a UUID")

        datasource = self.db.get_datasource_raw(datasource_id)
        if not datasource:
            raise ValueError("datasource not found for entity")

        implementation = datasource.get("implementation")
        if not implementation:
            raise ValueError("datasource implementation not specified")

        entity_config = entity.get("config")
        datasource_config = datasource.get("config")
        # merge configs, with entity config taking precedence
        config = {
            **(dict(datasource_config) if isinstance(datasource_config, dict) else {}),
            **(dict(entity_config) if isinstance(entity_config, dict) else {})
        }

        ds = _load_datasource(str(implementation), config)

        # pass entity (raw mapping) to implementation
        prices: Iterable[PriceRecord] = ds.fetch_prices(entity, start, end)

        # if single price data - verify if timestamp is not today or in the future - could be incomplete or bogus data
        # if OLHC data - verify if timestamp range does not extend into future - could be incomplete data
        now = datetime.now()
        cleaned_prices = []
        for price in prices:
            if price.timestamp and price.timestamp > now:
                # single price record
                # discard price data that is from today or in the future
                print(f"{datetime.now().isoformat()} : Discarding price record with timestamp {price.timestamp} for entity_id {db_entity_id} as it is from today or in the future for date range {start} to {end}")
                continue
            elif price.timestamp_start and price.timestamp_end and (price.timestamp_start > now or price.timestamp_end > now):
                # OHLC record
                # discard price data that is from today or in the future based on start and end timestamps
                print(f"{datetime.now().isoformat()} : Discarding price record with start timestamp {price.timestamp_start} and end timestamp {price.timestamp_end} for entity_id {db_entity_id} as it is from today or in the future for date range {start} to {end}")
                continue
            cleaned_prices.append(price)

        # save prices to database and return number of records inserted
        print(f"{datetime.now().isoformat()} : Inserting {len(cleaned_prices)} price records for entity_id {db_entity_id} and date range {start} to {end}")
        inserted = self.db.save_prices(db_entity_id, cleaned_prices)
        return inserted

    def query_entities(self, entity_id: Optional[UUID], entity_code: Optional[str], entity_name: Optional[str], frequency: Optional[str]) -> Iterable[EntityRecord]:
        return self.db.query_entities(entity_id, entity_code, entity_name, frequency)

    def query_prices(self, entity_id: UUID, start: datetime, end: datetime, page: int = 0, size: int = 10) -> Iterable[PriceRecord]:
        entity = self.db.get_entity_by_id_raw(entity_id)
        if not entity:
            raise ValueError("entity not found")

        # ensure entity id is a UUID instance
        return self.db.query_prices(entity_id, start, end, page, size)

    def query_close_series(self, entity_id: UUID, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
        """Fetch the full ordered close/price series for an entity and date range.

        Returns a list of (timestamp, value) tuples ordered by timestamp ascending.
        Used by moving-average computations which need the full window.
        """
        return self.db.query_close_series(entity_id, start, end)

    def query_price_summary(self, entity_id: UUID, start: datetime, end: datetime) -> Optional[dict[str, object]]:
        """Query aggregate summary statistics for a price series in a date range."""
        return self.db.query_price_summary(entity_id, start, end)

    def _prices_exist(self, entity: dict[str, object], start: datetime, end: datetime) -> bool:
        entity_id = entity.get("id")
        if not entity_id:
            raise ValueError("entity must have an id")

        # ensure entity id is a UUID instance
        if not isinstance(entity_id, UUID):
            entity_id = UUID(str(entity_id))

        # get expected number of price records based on entity frequency and date range
        frequency = entity.get("frequency")
        if not frequency or not isinstance(frequency, str):
            raise ValueError("entity must have a frequency")
        # continuous frequency means we can't determine expected count, so always fetch
        if frequency == "CONTINUOUS":
            return False
        has_weekend = bool(entity.get("has_weekend", False))

        expected_count = self._expected_price_count(frequency, has_weekend, start, end)
        if expected_count is None:
            raise ValueError(f"unsupported frequency: {frequency}")

        # query actual count of price records for entity and date range
        actual_count = self.db.count_prices(entity_id, start, end)
        print(f"{datetime.now().isoformat()} : Expected price count: {expected_count}, actual price count: {actual_count} for entity_id {entity_id} and date range {start} to {end}")
        return actual_count >= expected_count

    def _expected_price_count(self, frequency: str, has_weekend: bool, start: datetime, end: datetime) -> Optional[int]:
        if frequency == "DAILY":
            count = 0
            current = start
            while current < end:
                if has_weekend or current.weekday() < 5:
                    count += 1
                current += timedelta(days=1)
            return count
        # TODO: Add support for other frequencies as needed
        return None

    # entity_fundamentals methods
    def upload_fundamentals(
        self,
        entity_id: UUID,
        metrics: dict,
        source_name: str,
        source_url: str,
        as_of_date: Optional[str],
        confidence: str,
        notes: str,
    ) -> dict:
        """Upload a fundamentals snapshot for an entity.

        Generates a snapshot_id, inserts into entity_fundamentals, and returns
        the created record as a dict.
        """
        from uuid import uuid4
        snapshot_id = "fnd_" + uuid4().hex[:8]
        row = self.db.insert_fundamentals(
            id=snapshot_id,
            entity_id=entity_id,
            metrics=metrics,
            source_name=source_name,
            source_url=source_url,
            as_of_date=as_of_date,
            confidence=confidence,
            notes=notes,
            uploaded_by="agent",
        )
        return row

    def get_fundamentals(
        self, entity_code: str, latest_only: bool = True
    ) -> tuple[Optional[dict], list[dict]]:
        """Resolve entity_code and return (entity_raw, fundamentals_rows).

        Returns (None, []) if entity not found.
        """
        entities = self.query_entities(None, entity_code, None, None)
        entities_list = list(entities)
        if not entities_list:
            return None, []
        entity = entities_list[0]
        entity_id = entity.id
        rows = self.db.get_fundamentals_by_entity(entity_id, latest_only=latest_only)
        # Return the raw entity dict from DB for richer data (name, etc.)
        entity_raw = self.db.get_entity_by_id_raw(entity_id)
        return entity_raw, rows