"""Uniswap Pool v3 DataSource implementation.

This datasource fetches price data from a Uniswap v3 pool.
It extends the Web3DataSource and delegates to the Uniswap
utility functions to obtain the price at the current time.
"""

from decimal import Decimal
from typing import cast

from web3 import Web3

from price_sources.web3.erc20_token import ERC20Token
from price_sources.web3.uniswap import (
    get_uniswap_pool_v3_quoter_swap_price,
    get_uniswap_pool_v3_quoter_v2_swap_price,
)
from price_sources.web3_datasource import Web3DataSource


class UniswapDataSource(Web3DataSource):

    def __init__(self, config: dict):
        super().__init__(config)
        self.use_v2 = bool(self.config.get("use_v2"))

        if not self.config.get("source_token_address"):
            raise ValueError("config must include 'source_token_address'")
        self.source_token_address: str = cast(
            str, self.config.get("source_token_address")
        )
        if not self.config.get("target_token_address"):
            raise ValueError("config must include 'target_token_address'")
        self.target_token_address: str = cast(
            str, self.config.get("target_token_address")
        )
        self.pool_fee: int = int(self.config.get("pool_fee", 0))

    def fetch_blockchain_price(
        self,
        contract_address: str,
        web3: Web3,
    ) -> Decimal:
        """Fetch the price of a token from Uniswap using the provided Web3 instance.
        Using the uniswap v3 pool at the quoter address with the specific fee, convert one unit of the
        from token to the to token.
        """
        from_token = ERC20Token(self.source_token_address, web3)
        to_token = ERC20Token(self.target_token_address, web3)

        if self.use_v2:
            return get_uniswap_pool_v3_quoter_v2_swap_price(
                contract_address=contract_address,
                from_token=from_token,
                to_token=to_token,
                pool_fee=self.pool_fee,
                amount=Decimal("1.0"),
                web3=web3,
            )
        else:
            return get_uniswap_pool_v3_quoter_swap_price(
                contract_address=contract_address,
                from_token=from_token,
                to_token=to_token,
                pool_fee=self.pool_fee,
                amount=Decimal("1.0"),
                web3=web3,
            )
