import weakref
from unittest.mock import Mock

import pytest

from price_sources.web3.erc20_token import ERC20Token

TOKEN_ADDRESS = "0x" + "1" * 40
RPC_URL = "https://rpc.example.test"


class _Provider:
    endpoint_uri = RPC_URL


class _Web3:
    def __init__(self):
        self.provider = _Provider()


def _web3():
    return _Web3()


def _contract(name="Example Token", decimals=6, symbol="EXT"):
    contract = Mock()
    contract.functions.name().call.return_value = name
    contract.functions.decimals().call.return_value = decimals
    contract.functions.symbol().call.return_value = symbol
    return contract


def test_initialization_loads_contract_and_metadata(monkeypatch):
    contract = _contract()
    get_cached_contract = Mock(return_value=contract)
    monkeypatch.setattr(
        "price_sources.web3.erc20_token.get_cached_contract", get_cached_contract
    )

    token = ERC20Token(TOKEN_ADDRESS, _web3())

    assert token.address == TOKEN_ADDRESS
    assert token.rpc_url == RPC_URL
    assert token.name == "Example Token"
    assert token.decimals == 6
    assert token.symbol == "EXT"
    get_cached_contract.assert_called_once_with(
        RPC_URL, TOKEN_ADDRESS, "ERC20_ABI.json", web3=token._web3
    )


def test_metadata_methods_are_called_once_in_expected_order(monkeypatch):
    contract = _contract()
    calls = []
    contract.functions.name().call.side_effect = lambda: calls.append("name") or "N"
    contract.functions.decimals().call.side_effect = lambda: (
        calls.append("decimals") or 18
    )
    contract.functions.symbol().call.side_effect = lambda: (
        calls.append("symbol") or "SYM"
    )
    monkeypatch.setattr(
        "price_sources.web3.erc20_token.get_cached_contract",
        Mock(return_value=contract),
    )

    ERC20Token(TOKEN_ADDRESS, _web3())

    assert calls == ["name", "decimals", "symbol"]


def test_to_string_formats_token_identity(monkeypatch):
    monkeypatch.setattr(
        "price_sources.web3.erc20_token.get_cached_contract",
        Mock(return_value=_contract(name="USD Coin", decimals=6, symbol="USDC")),
    )

    token = ERC20Token(TOKEN_ADDRESS, _web3())

    assert token.to_string() == f"{TOKEN_ADDRESS} - USDC - USD Coin - 10^6"


def test_initialization_rejects_missing_contract(monkeypatch):
    monkeypatch.setattr(
        "price_sources.web3.erc20_token.get_cached_contract", Mock(return_value=None)
    )

    with pytest.raises(
        ValueError,
        match=f"Failed to create contract instance for address: {TOKEN_ADDRESS}",
    ):
        ERC20Token(TOKEN_ADDRESS, _web3())


@pytest.mark.parametrize(
    "name, decimals, symbol",
    [("", 0, ""), ("Long Token Name", 18, "LTN"), ("Token", 8, "T")],
)
def test_metadata_values_are_preserved(monkeypatch, name, decimals, symbol):
    monkeypatch.setattr(
        "price_sources.web3.erc20_token.get_cached_contract",
        Mock(return_value=_contract(name, decimals, symbol)),
    )

    token = ERC20Token(TOKEN_ADDRESS, _web3())

    assert (token.name, token.decimals, token.symbol) == (name, decimals, symbol)


def test_metadata_cache_does_not_cross_web3_instances(monkeypatch):
    first_contract = _contract(name="First", decimals=6, symbol="ONE")
    second_contract = _contract(name="Second", decimals=18, symbol="TWO")
    monkeypatch.setattr(
        "price_sources.web3.erc20_token.get_cached_contract",
        Mock(side_effect=[first_contract, second_contract]),
    )

    first_web3 = _web3()
    second_web3 = _web3()
    first = ERC20Token(TOKEN_ADDRESS, first_web3)
    second = ERC20Token(TOKEN_ADDRESS, second_web3)

    assert (first.name, first.decimals, first.symbol) == ("First", 6, "ONE")
    assert (second.name, second.decimals, second.symbol) == ("Second", 18, "TWO")


def test_metadata_cache_retains_web3_identity(monkeypatch):
    web3 = _web3()
    contract = _contract(name="Retained", decimals=18, symbol="RET")
    monkeypatch.setattr(
        "price_sources.web3.erc20_token.get_cached_contract",
        Mock(return_value=contract),
    )

    token = ERC20Token(TOKEN_ADDRESS, web3)
    reference = weakref.ref(web3)
    del token
    del web3

    assert reference() is not None


# Provider/client construction and Web3 connectivity remain intentionally out of scope.
