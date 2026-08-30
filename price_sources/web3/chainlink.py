"""Web3 service for Chainlink price data."""

from decimal import Decimal
from typing import cast

from web3 import Web3

from price_sources.web3.web3 import get_cached_contract


def get_chainlink_price_feed_price(
    contract_address: str,
    web3: Web3,
) -> Decimal:
    provider: Web3.HTTPProvider = cast(Web3.HTTPProvider, web3.provider)
    rpc_url = str(provider.endpoint_uri)
    contract = get_cached_contract(
        rpc_url,
        contract_address,
        "Chainlink_Aggregator_Proxy_ABI.json",
        web3,
    )

    (
        _roundId,
        answer,
        _startedAt,
        _updatedAt,
        _answeredInRound,
    ) = contract.functions.latestRoundData().call()
    decimals = contract.functions.decimals().call()
    return Decimal(answer) / Decimal(10**decimals)