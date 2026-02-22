import importlib
from datetime import datetime
from typing import Iterable, Optional, Union
from uuid import UUID

from .db import Database
from .types import EntityRecord, PriceRecord
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

    def __init__(self, db: str):
        self.db = Database(db)

    def fetch_and_store(self, entity_id: Optional[UUID], entity_code: Optional[str], start: datetime, end: datetime) -> int:
        entity = None
        # try to lookup entity by id first
        if entity_id:
            entity = self.db.get_entity_by_id(entity_id)
        # try to lookup entity by code if not found by id
        if entity_code and not entity:
            entity = self.db.get_entity_by_code(entity_code)

        # if entity is still not found, raise an error
        if not entity:
            raise ValueError("entity not found")

        datasource = self.db.get_datasource(entity["datasource_id"])
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

        # ensure entity id is a UUID instance
        db_entity_id = entity["id"]
        if not isinstance(db_entity_id, UUID):
            db_entity_id = UUID(str(db_entity_id))
        inserted = self.db.save_prices(db_entity_id, prices)
        return inserted

    def query_entities(self, entity_id: UUID, entity_code: str, entity_name: str, frequency: str) -> Iterable[EntityRecord]:
        return self.db.query_entities(entity_id, entity_code, entity_name, frequency)

    def query_prices(self, entity_id: UUID, start: datetime, end: datetime) -> Iterable[PriceRecord]:
        entity = self.db.get_entity_by_id(entity_id)
        if not entity:
            raise ValueError("entity not found")

        # ensure entity id is a UUID instance
        return self.db.fetch_prices(entity_id, start, end)
