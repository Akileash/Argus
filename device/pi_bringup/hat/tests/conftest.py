"""Test setup: provide a stub smbus2 on laptops where it is not installed."""

import sys
import types

try:
    import smbus2  # noqa: F401
except ImportError:  # pragma: no cover
    stub = types.ModuleType("smbus2")
    stub.SMBus = object
    sys.modules["smbus2"] = stub
