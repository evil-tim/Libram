import json
import threading
import time
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from typing import Any

from eth_typing import ChecksumAddress
from web3 import Web3
from web3.contract import Contract

"""Utility functions for Web3 service helpers."""


_CONTRACT_CACHE_MAXSIZE = 32
_WEB3_CACHE_MAXSIZE = 8

# These are explicit caches rather than lru_cache instances because a failed
# provider must be evicted by its own key. functools.lru_cache only exposes a
# global cache_clear(), which would discard healthy providers as well.
_web3_cache: OrderedDict[tuple[str, int], Web3] = OrderedDict()
_contract_cache: OrderedDict[tuple[int, str, str], Contract] = OrderedDict()
_cache_lock = threading.Lock()


@lru_cache(maxsize=8)
def get_cached_contract_abi(filename: str) -> list[dict[str, Any]]:
    abi_path = Path(__file__).with_name(filename)
    with abi_path.open("r", encoding="utf-8") as content_file:
        return json.load(content_file)


def get_cached_contract(rpc_url: str, address: str, abi_filename: str) -> Contract:
    """Return a contract cached against the specific Web3 instance it uses."""
    web3 = get_web3_instance(rpc_url)
    cache_key = (id(web3), address, abi_filename)

    # The cache lock protects only dictionary operations. Contract creation and
    # provider validation are deliberately outside it so a slow RPC endpoint
    # cannot block unrelated endpoints from reading or updating their entries.
    with _cache_lock:
        contract = _contract_cache.get(cache_key)
        if contract is not None:
            _contract_cache.move_to_end(cache_key)
            return contract

    checksum_address = _normalize_address(web3, address)
    contract = web3.eth.contract(
        address=checksum_address,
        abi=get_cached_contract_abi(abi_filename),
    )

    # Another thread may have created the same contract while we were doing
    # local work. Reuse the winner so callers converge on one cached object.
    with _cache_lock:
        existing = _contract_cache.get(cache_key)
        if existing is not None:
            _contract_cache.move_to_end(cache_key)
            return existing
        _contract_cache[cache_key] = contract
        _contract_cache.move_to_end(cache_key)
        while len(_contract_cache) > _CONTRACT_CACHE_MAXSIZE:
            _contract_cache.popitem(last=False)
    return contract


def _normalize_address(web3: Web3, address: str) -> ChecksumAddress:
    """Normalize and validate an Ethereum address, returning a checksum address.

    Accepts addresses with or without the '0x' prefix. Raises ValueError for
    invalid inputs so callers fail fast.
    """
    if not isinstance(address, str):
        raise TypeError("address must be a hex string")

    addr = address
    if not addr.startswith(("0x", "0X")):
        addr = "0x" + addr

    # basic validation: 40 hex chars after 0x
    hexpart = addr[2:]
    if len(hexpart) != 40 or any(c not in "0123456789abcdefABCDEF" for c in hexpart):
        raise ValueError(f"Invalid Ethereum address: {address!r}")

    try:
        return web3.to_checksum_address(addr)
    except Exception as exc:  # pragma: no cover - defensive
        raise ValueError(f"Invalid Ethereum address: {address!r}: {exc}") from exc


def _is_web3_valid(web3: Web3 | None) -> bool:
    return web3 is not None and web3.is_connected()


def _create_web3_instance(rpc_url: str, timeout_sec: int = 30) -> Web3:
    """Create a Web3 HTTP client.

    This helper intentionally does not cache. Cache lookup, validation, and
    replacement are coordinated by get_web3_instance below.
    """
    print("Connecting web3 - " + rpc_url)
    return Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": timeout_sec}))


def _cache_get_web3(rpc_url: str, timeout_sec: int) -> Web3 | None:
    with _cache_lock:
        key = (rpc_url, timeout_sec)
        web3 = _web3_cache.get(key)
        if web3 is not None:
            _web3_cache.move_to_end(key)
        return web3


def _cache_evict_web3(
    rpc_url: str, timeout_sec: int, expected: Web3 | None = None
) -> None:
    # Only remove the entry if it is still the object we tested. A concurrent
    # caller may already have installed a healthy replacement for this key.
    with _cache_lock:
        key = (rpc_url, timeout_sec)
        current = _web3_cache.get(key)
        if current is not None and (expected is None or current is expected):
            del _web3_cache[key]
            _evict_contracts_for_web3_id(id(current))


def _cache_store_web3(rpc_url: str, timeout_sec: int, web3: Web3) -> Web3:
    with _cache_lock:
        key = (rpc_url, timeout_sec)
        existing = _web3_cache.get(key)
        if existing is not None:
            # Another thread may have installed a replacement while this
            # thread was constructing or validating its client. Keep the
            # already-published instance instead of replacing a healthy one.
            _web3_cache.move_to_end(key)
            return existing
        _web3_cache[key] = web3
        _web3_cache.move_to_end(key)
        while len(_web3_cache) > _WEB3_CACHE_MAXSIZE:
            _, evicted_web3 = _web3_cache.popitem(last=False)
            _evict_contracts_for_web3_id(id(evicted_web3))
    return web3


def _evict_contracts_for_web3_id(web3_id: int) -> None:
    """Remove contracts belonging to one Web3 instance.

    Called while _cache_lock is held. It is kept separate to make that lock
    requirement obvious and prevent accidental nested locking.
    """
    for key in [key for key in _contract_cache if key[0] == web3_id]:
        del _contract_cache[key]


def get_web3_instance(
    rpc_url: str,
    retries: int = 3,
    timeout_sec: int = 30,
    backoff_sec: int = 5,
) -> Web3:
    """Get a validated Web3 client, retrying only the requested cache key."""
    for count in range(retries):
        # get from cache and return it if valid otherwise evict it from cache
        web3 = _cache_get_web3(rpc_url, timeout_sec)
        if web3 is not None:
            if _is_web3_valid(web3):
                return web3
            _cache_evict_web3(rpc_url, timeout_sec, expected=web3)

        # Do not hold _cache_lock while constructing or validating the client:
        # both operations may perform network work and would otherwise stall
        # every other RPC endpoint sharing this process.
        web3 = _create_web3_instance(rpc_url, timeout_sec)
        if _is_web3_valid(web3):
            return _cache_store_web3(rpc_url, timeout_sec, web3)

        _cache_evict_web3(rpc_url, timeout_sec, expected=web3)
        if count < retries - 1:
            print(
                f"Web3 instance is not connected on attempt {count + 1}/{retries}, retrying after backoff"
            )
            time.sleep(backoff_sec)

    raise RuntimeError("web3 is not connected")


__all__ = [
    "get_cached_contract",
    "get_cached_contract_abi",
    "get_web3_instance",
]


def _clear_caches() -> None:
    """Clear Web3/contract/ABI caches for deterministic tests and maintenance."""
    with _cache_lock:
        _web3_cache.clear()
        _contract_cache.clear()
    get_cached_contract_abi.cache_clear()
