import pytest

from price_sources.web3 import web3 as web3_module
from price_sources.web3_datasource import _read_optional_int_env


class DummyEth:
    def __init__(self):
        self.last_address = None

    def contract(self, address, abi):
        self.last_address = address
        return {"address": address, "abi": abi}


class DummyWeb3:
    def __init__(self):
        self.eth = DummyEth()

    def to_checksum_address(self, addr: str) -> str:
        # deterministic and simple checksum stand-in for tests
        return addr.lower()


def test_normalize_with_0x_prefix():
    dummy = DummyWeb3()
    addr = "0x" + "a" * 40
    out = web3_module._normalize_address(dummy, addr)
    assert out == dummy.to_checksum_address(addr)


def test_normalize_without_0x_prefix():
    dummy = DummyWeb3()
    raw = "a" * 40
    out = web3_module._normalize_address(dummy, raw)
    assert out == dummy.to_checksum_address("0x" + raw)


def test_normalize_invalid_length():
    dummy = DummyWeb3()
    with pytest.raises(ValueError):
        web3_module._normalize_address(dummy, "0x" + "a" * 39)


def test_normalize_invalid_chars():
    dummy = DummyWeb3()
    # 'g' is not a valid hex char
    with pytest.raises(ValueError):
        web3_module._normalize_address(dummy, "0x" + "g" * 40)


def test_get_cached_contract_uses_normalized_address(monkeypatch):
    # Arrange: stub out dependencies
    dummy = DummyWeb3()
    web3_module._clear_caches()
    monkeypatch.setattr(web3_module, "get_web3_instance", lambda rpc: dummy)
    monkeypatch.setattr(
        web3_module, "get_cached_contract_abi", lambda fn: [{"dummy": True}]
    )

    # Act
    raw = "Aa" * 20  # mixed-case hex
    result = web3_module.get_cached_contract("http://rpc", raw, "abi.json")

    # Assert: contract factory received checksumed address via our dummy
    assert result["address"] == dummy.to_checksum_address("0x" + raw)
    assert result["abi"] == [{"dummy": True}]


def test_read_optional_int_env_parses_integer(monkeypatch):
    monkeypatch.setenv("LIBRAM_WEB3_RETRIES", "4")

    assert _read_optional_int_env("LIBRAM_WEB3_RETRIES") == 4


def test_read_optional_int_env_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv("LIBRAM_WEB3_RETRIES", raising=False)

    assert _read_optional_int_env("LIBRAM_WEB3_RETRIES") is None


def test_read_optional_int_env_rejects_non_integer(monkeypatch):
    monkeypatch.setenv("LIBRAM_WEB3_RETRIES", "three")

    with pytest.raises(ValueError, match="LIBRAM_WEB3_RETRIES must be an integer"):
        _read_optional_int_env("LIBRAM_WEB3_RETRIES")


class _ConnectedWeb3(DummyWeb3):
    def __init__(self, connected=True):
        super().__init__()
        self.connected = connected

    def is_connected(self):
        return self.connected


def test_web3_cache_reuses_connected_instance(monkeypatch):
    web3_module._clear_caches()
    instances = [_ConnectedWeb3()]
    monkeypatch.setattr(
        web3_module, "_get_cached_web3_instance", lambda *args: instances[0]
    )
    monkeypatch.setattr(
        web3_module, "_is_web3_valid", lambda value: value.is_connected()
    )

    first = web3_module.get_web3_instance("https://rpc-a", retries=1)
    second = web3_module.get_web3_instance("https://rpc-a", retries=1)

    assert first is second


def test_web3_cache_keys_timeout_separately(monkeypatch):
    web3_module._clear_caches()
    created = []

    def make_web3(rpc_url, timeout_sec):
        value = _ConnectedWeb3()
        created.append((rpc_url, timeout_sec, value))
        return value

    monkeypatch.setattr(web3_module, "_get_cached_web3_instance", make_web3)
    monkeypatch.setattr(
        web3_module, "_is_web3_valid", lambda value: value.is_connected()
    )

    first = web3_module.get_web3_instance("https://rpc-a", timeout_sec=10, retries=1)
    second = web3_module.get_web3_instance("https://rpc-a", timeout_sec=20, retries=1)

    assert first is not second
    assert [(rpc, timeout) for rpc, timeout, _ in created] == [
        ("https://rpc-a", 10),
        ("https://rpc-a", 20),
    ]


def test_invalid_endpoint_only_evicts_its_own_cache_entry(monkeypatch):
    web3_module._clear_caches()
    healthy = _ConnectedWeb3()
    invalid = _ConnectedWeb3(connected=False)
    created = {"https://rpc-a": healthy, "https://rpc-b": invalid}
    monkeypatch.setattr(
        web3_module,
        "_get_cached_web3_instance",
        lambda rpc_url, timeout_sec: created[rpc_url],
    )
    monkeypatch.setattr(
        web3_module, "_is_web3_valid", lambda value: value.is_connected()
    )

    assert web3_module.get_web3_instance("https://rpc-a", retries=1) is healthy
    with pytest.raises(RuntimeError, match="not connected"):
        web3_module.get_web3_instance("https://rpc-b", retries=1)

    assert web3_module._cache_get_web3("https://rpc-a", 30) is healthy
    assert web3_module._cache_get_web3("https://rpc-b", 30) is None


def test_contract_cache_is_bound_to_web3_instance(monkeypatch):
    web3_module._clear_caches()
    first_web3 = _ConnectedWeb3()
    second_web3 = _ConnectedWeb3()
    current = [first_web3]
    monkeypatch.setattr(web3_module, "get_web3_instance", lambda rpc_url: current[0])
    monkeypatch.setattr(
        web3_module,
        "get_cached_contract_abi",
        lambda filename: [{"name": filename}],
    )

    first_contract = web3_module.get_cached_contract(
        "https://rpc-a", "a" * 40, "abi.json"
    )
    current[0] = second_web3
    second_contract = web3_module.get_cached_contract(
        "https://rpc-a", "a" * 40, "abi.json"
    )

    assert first_contract is not second_contract
