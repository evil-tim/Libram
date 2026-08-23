from abc import ABC
from collections.abc import Iterable
from datetime import datetime

from libram_types.libram_types import PriceRecord


class BaseDatasource(ABC):
    """Interface for datasource implementations.

    Implementations should subclass this and override `fetch_prices` for historical price implementations
    and/or `fetch_price` for snapshot price implementations.
    """

    def __init__(self, config: dict):
        self.config = config or {}

    def fetch_prices(
        self, entity: dict, start: datetime, end: datetime
    ) -> Iterable[PriceRecord]:
        """Fetch price data for `entity` between `start` and `end`.

        `entity` is the raw DB row (as a mapping/dict) from the `entity` table.
        Real implementations must yield `PriceRecord` instances.
        """
        raise UnsupportedDatasourceOperationError(
            "This datasource does not support historical price fetching."
        )

    def fetch_price(self, entity: dict) -> PriceRecord:
        """Fetch the current price data for `entity`.

        `entity` is the raw DB row (as a mapping/dict) from the `entity` table.
        Real implementations must yield a `PriceRecord` instance.
        """
        raise UnsupportedDatasourceOperationError(
            "This datasource does not support snapshot price fetching."
        )


class UnsupportedDatasourceOperationError(Exception):
    pass
