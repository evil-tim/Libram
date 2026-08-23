"""Base Web3 datasource implementation. Contains common logic for fetching prices from blockchain sources using Web3.
This class is intended to be subclassed by specific implementations that provide the actual price fetching logic using two ERC20 tokens as
the source and target tokens.
"""

import os
from abc import abstractmethod
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

from web3 import Web3

from libram_types.libram_types import PriceRecord
from price_management import BaseDatasource
from price_sources.web3.erc20_token import ERC20Token
from price_sources.web3.web3 import get_web3_instance


class Web3DataSource(BaseDatasource):

    def __init__(self, config: dict):
        super().__init__(config)
        if not self.config.get("rpc_url"):
            raise ValueError("config must include 'rpc_url'")
        self.rpc_url: str = cast(str, self.config.get("rpc_url"))
        if not self.config.get("contract_address"):
            raise ValueError("config must include 'contract_address'")
        self.contract_address: str = cast(str, self.config.get("contract_address"))
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
        self.retries: int | None = (
            cast(int, os.getenv("LIBRAM_WEB3_RETRIES"))
            if os.getenv("LIBRAM_WEB3_RETRIES") is not None
            else None
        )
        self.timeout: int | None = (
            cast(int, os.getenv("LIBRAM_WEB3_TIMEOUT"))
            if os.getenv("LIBRAM_WEB3_TIMEOUT") is not None
            else None
        )
        self.backoff: int | None = (
            cast(int, os.getenv("LIBRAM_WEB3_BACKOFF"))
            if os.getenv("LIBRAM_WEB3_BACKOFF") is not None
            else None
        )

    def fetch_prices(
        self, entity: dict, start: datetime, end: datetime
    ) -> Iterable[PriceRecord]:
        """Fetch price data for a blockchain token. Entity is implied from the configured source token.
        Entity's currency is implied from the configured target token. Start and end are ignored - this is the price
        for the current block or latest available data."""

        web3 = get_web3_instance(
            self.rpc_url,
            self.retries if self.retries is not None else 3,
            self.timeout if self.timeout is not None else 30,
            self.backoff if self.backoff is not None else 5,
        )
        source_token = ERC20Token(self.source_token_address, web3)
        target_token = ERC20Token(self.target_token_address, web3)

        price = self.fetch_blockchain_price(
            contract_address=self.contract_address,
            from_token=source_token,
            to_token=target_token,
            pool_fee=self.pool_fee,
            web3=web3,
        )

        record = PriceRecord(
            price=price,
            timestamp=datetime.now(),
        )
        return [record]

    @abstractmethod
    def fetch_blockchain_price(
        self,
        contract_address: str,
        from_token: ERC20Token,
        to_token: ERC20Token,
        pool_fee: int,
        web3: Web3,
    ) -> Decimal:
        """Fetch the price of a token from the blockchain or indexer.
        This is a placeholder for the actual implementation."""
        raise NotImplementedError()
