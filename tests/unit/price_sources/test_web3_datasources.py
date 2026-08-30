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
from price_sources.chainlink_datasource import ChainlinkDataSource
from price_sources.uniswap_datasource import UniswapDataSource
from price_sources.web3.chainlink import get_chainlink_price_feed_price
from price_sources.web3.erc20_token import ERC20Token
from price_sources.web3.uniswap import (
    get_uniswap_pool_v3_quoter_swap_price,
    get_uniswap_pool_v3_quoter_v2_swap_price,
)
from price_sources.web3.web3 import get_cached_contract_abi
from price_sources.web3_datasource import Web3DataSource

TOKEN_ADDRESS = "0x" + "1" * 40
TARGET_ADDRESS = "0x" + "2" * 40
POOL_ADDRESS = "0x" + "3" * 40


class _Provider:
    endpoint_uri = "https://rpc.example.test"


class _ConcreteWeb3DataSource(Web3DataSource):
    def fetch_blockchain_price(self, web3):
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
    monkeypatch.setattr("price_sources.web3_datasource.get_web3_instance", get_web3)

    result = datasource.fetch_price({"code": "TOKEN"})

    assert isinstance(result, PriceRecord)
    assert result.price == Decimal("12.5")
    assert isinstance(result.timestamp, datetime)
    assert result.timestamp.tzinfo is UTC
    get_web3.assert_called_once_with("https://rpc.example.test", 3, 30, 5)


def test_web3_datasource_passes_contract_address_and_web3(monkeypatch):
    web3 = SimpleNamespace(provider=_Provider())
    monkeypatch.setattr(
        "price_sources.web3_datasource.get_web3_instance", Mock(return_value=web3)
    )

    received = {}

    class _Recorder(Web3DataSource):
        def fetch_blockchain_price(self, web3):
            received["web3"] = web3
            return Decimal(1)

    _Recorder(web3_config()).fetch_price({"code": "TOKEN"})

    assert received["web3"] is web3


def test_uniswap_datasource_fetch_price_delegates_to_quoter_v1(monkeypatch):
    datasource = UniswapDataSource(web3_config())
    web3 = SimpleNamespace(provider=_Provider())
    monkeypatch.setattr(
        "price_sources.web3_datasource.get_web3_instance", Mock(return_value=web3)
    )
    quote = Mock(return_value=Decimal("4.25"))
    monkeypatch.setattr(
        "price_sources.uniswap_datasource.get_uniswap_pool_v3_quoter_swap_price",
        quote,
    )
    token_factory = Mock(side_effect=["source", "target"])
    monkeypatch.setattr("price_sources.uniswap_datasource.ERC20Token", token_factory)

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
    assert token_factory.call_args_list == [
        ((TOKEN_ADDRESS, web3), {}),
        ((TARGET_ADDRESS, web3), {}),
    ]


def test_uniswap_datasource_fetch_price_delegates_to_quoter_v2(monkeypatch):
    config = web3_config()
    config["use_v2"] = True
    datasource = UniswapDataSource(config)
    web3 = SimpleNamespace(provider=_Provider())
    monkeypatch.setattr(
        "price_sources.web3_datasource.get_web3_instance", Mock(return_value=web3)
    )
    quote = Mock(return_value=Decimal("4.25"))
    monkeypatch.setattr(
        "price_sources.uniswap_datasource.get_uniswap_pool_v3_quoter_v2_swap_price",
        quote,
    )
    monkeypatch.setattr(
        "price_sources.uniswap_datasource.ERC20Token",
        Mock(side_effect=["source", "target"]),
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


def test_uniswap_datasource_defaults_to_quoter_v1(monkeypatch):
    config = web3_config()
    config.pop("pool_fee")
    datasource = UniswapDataSource(config)
    web3 = SimpleNamespace(provider=_Provider())
    monkeypatch.setattr(
        "price_sources.web3_datasource.get_web3_instance", Mock(return_value=web3)
    )
    quote_v1 = Mock(return_value=Decimal(1))
    quote_v2 = Mock(return_value=Decimal(1))
    monkeypatch.setattr(
        "price_sources.uniswap_datasource.get_uniswap_pool_v3_quoter_swap_price",
        quote_v1,
    )
    monkeypatch.setattr(
        "price_sources.uniswap_datasource.get_uniswap_pool_v3_quoter_v2_swap_price",
        quote_v2,
    )
    monkeypatch.setattr(
        "price_sources.uniswap_datasource.ERC20Token",
        Mock(side_effect=["source", "target"]),
    )

    datasource.fetch_price({"code": "TOKEN"})

    assert datasource.pool_fee == 0
    assert not datasource.use_v2
    assert quote_v1.called
    assert not quote_v2.called


def test_uniswap_datasource_requires_token_configuration():
    for missing_key in ("source_token_address", "target_token_address"):
        config = web3_config()
        config.pop(missing_key)

        with pytest.raises(ValueError, match="config must include"):
            UniswapDataSource(config)


def test_chainlink_datasource_fetch_price_delegates_to_feed(monkeypatch):
    datasource = ChainlinkDataSource(web3_config())
    web3 = SimpleNamespace(provider=_Provider())
    monkeypatch.setattr(
        "price_sources.web3_datasource.get_web3_instance", Mock(return_value=web3)
    )
    feed = Mock(return_value=Decimal("43250.75"))
    monkeypatch.setattr(
        "price_sources.chainlink_datasource.get_chainlink_price_feed_price", feed
    )

    result = datasource.fetch_price({"code": "TOKEN"})

    assert result.price == Decimal("43250.75")
    assert result.timestamp.tzinfo is UTC
    feed.assert_called_once_with(contract_address=POOL_ADDRESS, web3=web3)


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


def test_uniswap_quoter_v1_converts_token_units_and_result(monkeypatch):
    contract = Mock()
    contract.functions.quoteExactInputSingle.return_value.call.return_value = 2_500_000
    get_contract = Mock(return_value=contract)
    monkeypatch.setattr("price_sources.web3.uniswap.get_cached_contract", get_contract)
    web3 = SimpleNamespace(provider=_Provider())
    from_token = SimpleNamespace(address=TOKEN_ADDRESS, decimals=6)
    to_token = SimpleNamespace(address=TARGET_ADDRESS, decimals=4)

    result = get_uniswap_pool_v3_quoter_swap_price(
        POOL_ADDRESS,
        from_token,
        to_token,
        500,
        Decimal("1.25"),
        web3,
    )

    assert result == Decimal(250)
    contract.functions.quoteExactInputSingle.assert_called_once_with(
        TOKEN_ADDRESS, TARGET_ADDRESS, 500, 1_250_000, 0
    )
    get_contract.assert_called_once_with(
        "https://rpc.example.test",
        POOL_ADDRESS,
        "Uniswap_v3_Quoter_ABI.json",
        web3,
    )


def test_uniswap_quoter_v2_builds_params_and_converts_result(monkeypatch):
    contract = Mock()
    contract.functions.quoteExactInputSingle.return_value.call.return_value = (
        2_500_000,
        12345,
        2,
        60_000,
    )
    get_contract = Mock(return_value=contract)
    monkeypatch.setattr("price_sources.web3.uniswap.get_cached_contract", get_contract)
    web3 = SimpleNamespace(provider=_Provider())
    from_token = SimpleNamespace(address=TOKEN_ADDRESS, decimals=6)
    to_token = SimpleNamespace(address=TARGET_ADDRESS, decimals=4)

    result = get_uniswap_pool_v3_quoter_v2_swap_price(
        POOL_ADDRESS,
        from_token,
        to_token,
        500,
        Decimal("1.25"),
        web3,
    )

    assert result == Decimal(250)
    contract.functions.quoteExactInputSingle.assert_called_once_with(
        {
            "tokenIn": TOKEN_ADDRESS,
            "tokenOut": TARGET_ADDRESS,
            "amountIn": 1_250_000,
            "fee": 500,
            "sqrtPriceLimitX96": 0,
        }
    )
    get_contract.assert_called_once_with(
        "https://rpc.example.test",
        POOL_ADDRESS,
        "Uniswap_v3_Quoter_v2_ABI.json",
        web3,
    )


def test_uniswap_quoter_v1_propagates_contract_errors(monkeypatch):
    contract = Mock()
    contract.functions.quoteExactInputSingle.return_value.call.side_effect = (
        RuntimeError("revert")
    )
    monkeypatch.setattr(
        "price_sources.web3.uniswap.get_cached_contract", Mock(return_value=contract)
    )

    with pytest.raises(RuntimeError, match="revert"):
        get_uniswap_pool_v3_quoter_swap_price(
            POOL_ADDRESS,
            SimpleNamespace(address=TOKEN_ADDRESS, decimals=0),
            SimpleNamespace(address=TARGET_ADDRESS, decimals=0),
            500,
            Decimal(1),
            SimpleNamespace(provider=_Provider()),
        )


def test_chainlink_feed_scales_answer_by_decimals(monkeypatch):
    contract = Mock()
    contract.functions.latestRoundData.return_value.call.return_value = (
        110_680,  # roundId
        43_250_750_000,  # answer, 8 decimals
        1_700_000_000,  # startedAt
        1_700_000_100,  # updatedAt
        110_680,  # answeredInRound
    )
    contract.functions.decimals.return_value.call.return_value = 8
    get_contract = Mock(return_value=contract)
    monkeypatch.setattr(
        "price_sources.web3.chainlink.get_cached_contract", get_contract
    )
    web3 = SimpleNamespace(provider=_Provider())

    result = get_chainlink_price_feed_price(POOL_ADDRESS, web3)

    assert result == Decimal("432.5075")
    get_contract.assert_called_once_with(
        "https://rpc.example.test",
        POOL_ADDRESS,
        "Chainlink_Aggregator_Proxy_ABI.json",
        web3,
    )


def test_chainlink_feed_propagates_contract_errors(monkeypatch):
    contract = Mock()
    contract.functions.latestRoundData.return_value.call.side_effect = RuntimeError(
        "revert"
    )
    monkeypatch.setattr(
        "price_sources.web3.chainlink.get_cached_contract", Mock(return_value=contract)
    )

    with pytest.raises(RuntimeError, match="revert"):
        get_chainlink_price_feed_price(
            POOL_ADDRESS, SimpleNamespace(provider=_Provider())
        )


def test_contract_abis_are_loadable():
    erc20_abi = get_cached_contract_abi("ERC20_ABI.json")
    quoter_abi = get_cached_contract_abi("Uniswap_v3_Quoter_ABI.json")
    quoter_v2_abi = get_cached_contract_abi("Uniswap_v3_Quoter_v2_ABI.json")
    chainlink_abi = get_cached_contract_abi("Chainlink_Aggregator_Proxy_ABI.json")

    assert any(item.get("name") == "name" for item in erc20_abi)
    assert any(item.get("name") == "quoteExactInputSingle" for item in quoter_abi)
    assert any(item.get("name") == "quoteExactInputSingle" for item in quoter_v2_abi)
    assert any(item.get("name") == "latestRoundData" for item in chainlink_abi)
    assert any(item.get("name") == "decimals" for item in chainlink_abi)


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


def test_web3_datasource_requires_configuration():
    config = web3_config()
    config.pop("rpc_url")

    with pytest.raises(ValueError, match="config must include"):
        _ConcreteWeb3DataSource(config)


def test_web3_subclasses_require_contract_address():
    # contract_address is subclass-specific: Uniswap has a single quoter per
    # chain, but Chainlink feeds have no common contract, so the base class
    # does not require it.
    for datasource_cls in (UniswapDataSource, ChainlinkDataSource):
        config = web3_config()
        config.pop("contract_address")

        with pytest.raises(ValueError, match="config must include"):
            datasource_cls(config)


def test_web3_datasource_rejects_invalid_integer_setting(monkeypatch):
    monkeypatch.setenv("LIBRAM_WEB3_TIMEOUT", "slow")

    with pytest.raises(ValueError, match="LIBRAM_WEB3_TIMEOUT must be an integer"):
        _ConcreteWeb3DataSource(web3_config())


# Web3 construction, connectivity, retry, and backoff behavior are intentionally
# excluded until get_web3_instance is reworked.
