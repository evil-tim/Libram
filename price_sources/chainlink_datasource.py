"""Chainlink DataSource implementation.

This datasource fetches price data from a Chainlink Price Feed.
It extends the Web3DataSource and delegates to the Chainlink
utility functions to obtain the price at the current time.
"""

from decimal import Decimal
from typing import cast

from web3 import Web3

from price_sources.web3.chainlink import get_chainlink_price_feed_price
from price_sources.web3_datasource import Web3DataSource


class ChainlinkDataSource(Web3DataSource):

    def __init__(self, config: dict):
        super().__init__(config)

        if not self.config.get("contract_address"):
            raise ValueError("config must include 'contract_address'")
        self.contract_address: str = cast(str, self.config.get("contract_address"))

        self.invert = bool(self.config.get("invert"))

    def fetch_blockchain_price(
        self,
        web3: Web3,
    ) -> Decimal:
        """Fetch the price of a token from Chainlink using the provided Web3 instance.
        Using the price feed contract address get the latest round data.
        """

        result = get_chainlink_price_feed_price(
            contract_address=self.contract_address, web3=web3
        )

        if self.invert:
            return 1 / result
        else:
            return result
