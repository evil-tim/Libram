from abc import ABC, abstractmethod
from datetime import datetime
from typing import Iterable

from libram_types.libram_types import PriceRecord


class BaseDatasource(ABC):
    """Interface for datasource implementations.

    Implementations should subclass this and provide `fetch_prices` which
    returns an iterable of `PriceRecord` for the given entity and time range.
    """

    def __init__(self, config: dict):
        self.config = config or {}

    @abstractmethod
    # TODO: make start and end optional for datasources that don't support historical fetching
    def fetch_prices(self, entity: dict, start: datetime, end: datetime) -> Iterable[PriceRecord]:
        """Fetch price data for `entity` between `start` and `end`.

        `entity` is the raw DB row (as a mapping/dict) from the `entity` table.
        Concrete implementations must yield `PriceRecord` instances.
        """
        raise NotImplementedError()
