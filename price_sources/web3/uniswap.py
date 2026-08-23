"""Web3 service for Uniswap price data."""

from decimal import Decimal
from typing import cast

from web3 import Web3

from price_sources.web3.erc20_token import ERC20Token
from price_sources.web3.web3 import get_cached_contract

"""Get the price of a token from Uniswap using the provided Web3 instance calling the quoteExactInputSingle function from the quoter contract."""


def get_uniswap_swap_price(
    contract_address: str,
    from_token: ERC20Token,
    to_token: ERC20Token,
    pool_fee: int,
    amount: Decimal,
    web3: Web3,
) -> Decimal:
    provider: Web3.HTTPProvider = cast(Web3.HTTPProvider, web3.provider)
    rpc_url = str(provider.endpoint_uri)
    contract = get_cached_contract(
        rpc_url,
        contract_address,
        "Uniswap_v3_Quoter_ABI.json",
    )

    result: int = contract.functions.quoteExactInputSingle(
        from_token.address,
        to_token.address,
        pool_fee,
        int(amount * (10**from_token.decimals)),
        0,
    ).call()
    return result / Decimal(10**to_token.decimals)
