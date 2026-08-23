import json
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from eth_typing import ChecksumAddress
from web3 import Web3
from web3.contract import Contract

"""Utility functions for Web3 service helpers."""


@lru_cache(maxsize=8)
def get_cached_contract_abi(filename: str) -> list[dict[str, Any]]:
    abi_path = Path(__file__).with_name(filename)
    with abi_path.open("r", encoding="utf-8") as content_file:
        return json.load(content_file)


@lru_cache(maxsize=32)
def get_cached_contract(rpc_url: str, address: str, abi_filename: str) -> Contract:
    web3 = get_web3_instance(rpc_url)
    checksum_address = _normalize_address(web3, address)
    return web3.eth.contract(
        address=checksum_address,
        abi=get_cached_contract_abi(abi_filename),
    )


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


@lru_cache(maxsize=8)
def _get_cached_web3_instance(rpc_url: str, timeout_sec: int = 30) -> Web3:
    print("Connecting web3 - " + rpc_url)
    return Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": timeout_sec}))


def _is_web3_valid(web3: Web3 | None) -> bool:
    return web3 is not None and web3.is_connected()


_lock = threading.Lock()

"""Get a Web3 instance, optionally reusing an existing one, with retries and backoff."""


# TODO if we are caching multiple instances with different rpc_urls, one failed instance
# will invalidate ALL instances, some of which are possibly still live. Convert into a
# per-key evict implementation
def get_web3_instance(
    rpc_url: str,
    retries: int = 3,
    timeout_sec: int = 30,
    backoff_sec: int = 5,
) -> Web3:
    # get or build instance with retries
    for count in range(retries):
        # gain exclusive lock
        with _lock:
            # get cached version or create new
            web3: Web3 | None = _get_cached_web3_instance(rpc_url, timeout_sec)
            if _is_web3_valid(web3):
                # return it if valid
                return web3
            else:
                # clear the cache so we get a new instance on the next call
                _get_cached_web3_instance.cache_clear()
        # release lock
        if count < retries - 1:
            # sleep
            print(
                f"Web3 instance is not connected on attempt {count + 1}/{retries}, retrying after backoff"
            )
            time.sleep(backoff_sec)
    # failed to get a valid web3 instance after retries
    raise RuntimeError("web3 is not connected")
