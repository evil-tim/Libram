from decimal import Decimal

from web3 import Web3

from price_sources.web3.erc20_token import ERC20Token
from price_sources.web3.uniswap import get_uniswap_swap_price
from price_sources.web3_datasource import Web3DataSource


class UniswapDataSource(Web3DataSource):
    def fetch_blockchain_price(
        self,
        contract_address: str,
        from_token: ERC20Token,
        to_token: ERC20Token,
        pool_fee: int,
        web3: Web3,
    ) -> Decimal:
        """Fetch the price of a token from Uniswap using the provided Web3 instance.
        Using the uniswap pool at the address with the specific fee, convert one unit of the
        from token to the to token.
        """
        return get_uniswap_swap_price(
            contract_address=contract_address,
            from_token=from_token,
            to_token=to_token,
            pool_fee=pool_fee,
            amount=Decimal("1.0"),
            web3=web3,
        )
