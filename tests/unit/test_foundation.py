"""Foundational checks for the test suite."""

import sys


def test_supported_python_version() -> None:
    """The project requires Python 3.14 or newer."""
    assert sys.version_info >= (3, 14)
