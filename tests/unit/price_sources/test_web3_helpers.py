import pytest

from price_sources.web3 import web3 as web3_module


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
    monkeypatch.setattr(web3_module, "get_web3_instance", lambda rpc: dummy)
    monkeypatch.setattr(web3_module, "get_cached_contract_abi", lambda fn: [{"dummy": True}])
    web3_module.get_cached_contract.cache_clear()

    # Act
    raw = "Aa" * 20  # mixed-case hex
    result = web3_module.get_cached_contract("http://rpc", raw, "abi.json")

    # Assert: contract factory received checksumed address via our dummy
    assert result["address"] == dummy.to_checksum_address("0x" + raw)
    assert result["abi"] == [{"dummy": True}]
