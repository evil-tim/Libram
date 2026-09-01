"""Kraken public ticker datasource for fetching current prices.

Extends the REST JSON datasource to call Kraken's public Ticker endpoint
(https://docs.kraken.com/api-reference/market-data/get-ticker-information).

The instance is initialized with a config dict. Expected config keys:
- "url" (required, from the REST base): the endpoint URL, e.g.
  "https://api.kraken.com/0/public/Ticker"
- "pair" (required): the Kraken display-name asset pair, e.g. "BTC/USD".
  With assetVersion=1, response keys use the slash-separated display format.
- "asset_version": optional, defaults to 1 (display names).
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from libram_types.libram_types import PriceRecord
from price_sources.rest_datasource import RestJSONDatasource


class KrakenTickerDataSource(RestJSONDatasource):
    """Fetch the current last-trade price from Kraken's public Ticker API."""

    def __init__(self, config: dict):
        super().__init__(config)

        # Specific config keys for the Kraken ticker datasource
        if not self.config.get("pair"):
            raise ValueError("config must include 'pair'")
        self.pair: str = str(self.config.get("pair"))
        self.asset_version: int = int(self.config.get("asset_version", 1))

    def build_request_params_snapshot(
        self,
        entity: dict,
        config: dict,
    ) -> tuple[str | None, dict[str, Any] | None, dict[str, Any] | None]:
        return (
            None,
            {
                "pair": self.pair,
                "assetVersion": self.asset_version,
            },
            None,
        )

    def parse_price_data_snapshot(
        self, data: dict[str, Any] | list
    ) -> PriceRecord:
        ticker = self._extract_ticker(data)
        price = self._parse_last_trade_price(ticker)

        return PriceRecord(
            price=price,
            timestamp=datetime.now(UTC),
        )

    def _extract_ticker(self, data: dict[str, Any] | list) -> dict[str, Any]:
        """Validate the Kraken response envelope and return the single pair ticker."""
        if not isinstance(data, dict):
            raise TypeError("Kraken response was not a JSON object")

        errors = data.get("error")
        if errors:
            raise ValueError(f"Kraken API error: {errors}")

        result = data.get("result")
        if not isinstance(result, dict) or not result:
            raise ValueError("Kraken response did not contain a result")

        if len(result) != 1:
            raise ValueError(
                f"Expected exactly one pair in Kraken response, got {sorted(result)}"
            )

        ticker = next(iter(result.values()))
        if not isinstance(ticker, dict):
            raise TypeError("Kraken ticker entry was not a JSON object")

        return ticker

    def _parse_last_trade_price(self, ticker: dict[str, Any]) -> Decimal:
        """Extract the last trade closed price (`c[0]`) from a ticker entry."""
        last_trade = ticker.get("c")
        if not isinstance(last_trade, list) or not last_trade:
            raise ValueError("Kraken ticker did not contain a last trade price")

        try:
            return Decimal(str(last_trade[0]))
        except Exception as exc:
            raise ValueError("Kraken last trade price was not a valid decimal") from exc
