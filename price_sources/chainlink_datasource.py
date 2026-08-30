"""Chainlink DataSource implementation.

This datasource fetches price data from a Chainlink Price Feed.
It extends the Web3DataSource and delegates to the Chainlink
utility functions to obtain the price at the current time.
"""

from decimal import Decimal

from web3 import Web3

from price_sources.web3.chainlink import get_chainlink_price_feed_price
from price_sources.web3_datasource import Web3DataSource


class ChainlinkDataSource(Web3DataSource):
    def fetch_blockchain_price(
        self,
        contract_address: str,
        web3: Web3,
    ) -> Decimal:
        """Fetch the price of a token from Chainlink using the provided Web3 instance.
        Using the price feed contract address get the latest round data.
        """

        return get_chainlink_price_feed_price(
            contract_address=contract_address, web3=web3
        )
