from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from libram_types import PriceRecord
from price_management.datasource import (
    BaseDatasource,
    UnsupportedDatasourceOperationError,
)
from price_sources.uniswap_datasource import UniswapDataSource
from price_sources.web3.erc20_token import ERC20Token
from price_sources.web3.uniswap import get_uniswap_swap_price
from price_sources.web3.web3 import get_cached_contract_abi
from price_sources.web3_datasource import Web3DataSource

TOKEN_ADDRESS = "0x" + "1" * 40
TARGET_ADDRESS = "0x" + "2" * 40
POOL_ADDRESS = "0x" + "3" * 40


class _Provider:
    endpoint_uri = "https://rpc.example.test"


class _ConcreteWeb3DataSource(Web3DataSource):
    def fetch_blockchain_price(
        self, contract_address, from_token, to_token, pool_fee, web3
    ):
        return Decimal("12.5")


def web3_config():
    return {
        "rpc_url": "https://rpc.example.test",
        "contract_address": POOL_ADDRESS,
        "source_token_address": TOKEN_ADDRESS,
        "target_token_address": TARGET_ADDRESS,
        "pool_fee": 500,
    }


def test_base_datasource_defaults_raise_capability_error():
    datasource = BaseDatasource({})

    with pytest.raises(UnsupportedDatasourceOperationError, match="historical"):
        datasource.fetch_prices({}, datetime.now(UTC), datetime.now(UTC))
    with pytest.raises(UnsupportedDatasourceOperationError, match="snapshot"):
        datasource.fetch_price({})


def test_web3_datasource_fetch_price_builds_snapshot(monkeypatch):
    datasource = _ConcreteWeb3DataSource(web3_config())
    web3 = SimpleNamespace(provider=_Provider())
    get_web3 = Mock(return_value=web3)
    token_factory = Mock(side_effect=["source", "target"])
    monkeypatch.setattr("price_sources.web3_datasource.get_web3_instance", get_web3)
    monkeypatch.setattr("price_sources.web3_datasource.ERC20Token", token_factory)

    result = datasource.fetch_price({"code": "TOKEN"})

    assert isinstance(result, PriceRecord)
    assert result.price == Decimal("12.5")
    assert isinstance(result.timestamp, datetime)
    assert result.timestamp.tzinfo is UTC
    get_web3.assert_called_once_with("https://rpc.example.test", 3, 30, 5)
    assert token_factory.call_args_list == [
        ((TOKEN_ADDRESS, web3), {}),
        ((TARGET_ADDRESS, web3), {}),
    ]


def test_uniswap_datasource_fetch_price_delegates_to_quote(monkeypatch):
    datasource = UniswapDataSource(web3_config())
    web3 = SimpleNamespace(provider=_Provider())
    monkeypatch.setattr(
        "price_sources.web3_datasource.get_web3_instance", Mock(return_value=web3)
    )
    monkeypatch.setattr(
        "price_sources.web3_datasource.ERC20Token",
        Mock(side_effect=["source", "target"]),
    )
    quote = Mock(return_value=Decimal("4.25"))
    monkeypatch.setattr(
        "price_sources.uniswap_datasource.get_uniswap_swap_price", quote
    )

    result = datasource.fetch_price({"code": "TOKEN"})

    assert result.price == Decimal("4.25")
    assert quote.call_args.kwargs == {
        "contract_address": POOL_ADDRESS,
        "from_token": "source",
        "to_token": "target",
        "pool_fee": 500,
        "amount": Decimal("1.0"),
        "web3": web3,
    }


def test_erc20_token_loads_metadata(monkeypatch):
    contract = Mock()
    contract.functions.name().call.return_value = "Example Token"
    contract.functions.decimals().call.return_value = 6
    contract.functions.symbol().call.return_value = "EXT"
    get_contract = Mock(return_value=contract)
    monkeypatch.setattr(
        "price_sources.web3.erc20_token.get_cached_contract", get_contract
    )

    token = ERC20Token(TOKEN_ADDRESS, SimpleNamespace(provider=_Provider()))

    assert token.name == "Example Token"
    assert token.decimals == 6
    assert token.symbol == "EXT"
    assert token.to_string() == f"{TOKEN_ADDRESS} - EXT - Example Token - 10^6"
    get_contract.assert_called_once_with(
        "https://rpc.example.test",
        TOKEN_ADDRESS,
        "ERC20_ABI.json",
        web3=token._web3,
    )


def test_uniswap_quote_converts_token_units_and_result(monkeypatch):
    contract = Mock()
    contract.functions.quoteExactInputSingle.return_value.call.return_value = 2_500_000
    monkeypatch.setattr(
        "price_sources.web3.uniswap.get_cached_contract", Mock(return_value=contract)
    )
    from_token = SimpleNamespace(address=TOKEN_ADDRESS, decimals=6)
    to_token = SimpleNamespace(address=TARGET_ADDRESS, decimals=4)

    result = get_uniswap_swap_price(
        POOL_ADDRESS,
        from_token,
        to_token,
        500,
        Decimal("1.25"),
        SimpleNamespace(provider=_Provider()),
    )

    assert result == Decimal(250)
    contract.functions.quoteExactInputSingle.assert_called_once_with(
        TOKEN_ADDRESS, TARGET_ADDRESS, 500, 1_250_000, 0
    )


def test_uniswap_quote_propagates_contract_errors(monkeypatch):
    contract = Mock()
    contract.functions.quoteExactInputSingle.return_value.call.side_effect = (
        RuntimeError("revert")
    )
    monkeypatch.setattr(
        "price_sources.web3.uniswap.get_cached_contract", Mock(return_value=contract)
    )

    with pytest.raises(RuntimeError, match="revert"):
        get_uniswap_swap_price(
            POOL_ADDRESS,
            SimpleNamespace(address=TOKEN_ADDRESS, decimals=0),
            SimpleNamespace(address=TARGET_ADDRESS, decimals=0),
            500,
            Decimal(1),
            SimpleNamespace(provider=_Provider()),
        )


def test_contract_abis_are_loadable():
    erc20_abi = get_cached_contract_abi("ERC20_ABI.json")
    quoter_abi = get_cached_contract_abi("Uniswap_v3_Quoter_ABI.json")

    assert any(item.get("name") == "name" for item in erc20_abi)
    assert any(item.get("name") == "quoteExactInputSingle" for item in quoter_abi)


@pytest.mark.parametrize(
    "env_name, value, attribute, expected",
    [
        ("LIBRAM_WEB3_RETRIES", "4", "retries", 4),
        ("LIBRAM_WEB3_TIMEOUT", "11", "timeout", 11),
        ("LIBRAM_WEB3_BACKOFF", "2", "backoff", 2),
    ],
)
def test_web3_datasource_reads_integer_settings(
    monkeypatch, env_name, value, attribute, expected
):
    monkeypatch.setenv(env_name, value)

    datasource = _ConcreteWeb3DataSource(web3_config())

    assert getattr(datasource, attribute) == expected


@pytest.mark.parametrize(
    "missing_key",
    ["rpc_url", "contract_address", "source_token_address", "target_token_address"],
)
def test_web3_datasource_requires_configuration(missing_key):
    config = web3_config()
    config.pop(missing_key)

    with pytest.raises(ValueError, match="config must include"):
        _ConcreteWeb3DataSource(config)


def test_web3_datasource_rejects_invalid_integer_setting(monkeypatch):
    monkeypatch.setenv("LIBRAM_WEB3_TIMEOUT", "slow")

    with pytest.raises(ValueError, match="LIBRAM_WEB3_TIMEOUT must be an integer"):
        _ConcreteWeb3DataSource(web3_config())


# Web3 construction, connectivity, retry, and backoff behavior are intentionally
# excluded until get_web3_instance is reworked.
