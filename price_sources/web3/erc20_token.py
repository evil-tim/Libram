from typing import cast

from web3 import Web3
from web3.contract import Contract

from price_sources.web3.web3 import get_cached_contract

"""ERC20Token class represents an ERC20 token on the Ethereum blockchain, providing methods to retrieve token information such as name, symbol,
and decimals using a Web3 instance. Only implements the ERC20 standard functions needed for price conversion, not the full ERC20 interface. The
class is initialized with the token's contract address and a Web3 instance, and it loads the token's information from the blockchain.
"""


class ERC20Token:

    def __init__(self, address: str, web3: Web3):
        self.address = address
        provider: Web3.HTTPProvider = cast(Web3.HTTPProvider, web3.provider)
        self.rpc_url = str(provider.endpoint_uri)
        self._get_contract()
        self._load_token_info()

    def _get_contract(self) -> None:
        contract: Contract = get_cached_contract(
            self.rpc_url, self.address, "ERC20_ABI.json"
        )
        if not contract:
            raise ValueError(
                "Failed to create contract instance for address: " + str(self.address)
            )
        self._contract = contract

    def _load_token_info(self) -> None:
        self.name: str = self._contract.functions.name().call()
        self.decimals: int = self._contract.functions.decimals().call()
        self.symbol: str = self._contract.functions.symbol().call()

    def to_string(self) -> str:
        return (
            str(self.address)
            + " - "
            + str(self.symbol)
            + " - "
            + str(self.name)
            + " - 10^"
            + str(self.decimals)
        )
