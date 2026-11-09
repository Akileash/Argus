"""Shared I2C bus wrapper with a lock and normalized faults.

Uses smbus2 on the Pi. Tests / --mock inject a FakeBus that implements
write_bytes / read_bytes / write_read — no smbus2 required.
"""

from __future__ import annotations

import threading
import time
from typing import List, Optional, Sequence


class SensorFault(Exception):
    """Any I2C / sensor communication failure."""


class I2CBus:
    """Thread-safe wrapper around /dev/i2c-N or a FakeBus backend."""

    def __init__(self, bus_num: int = 1, backend: Optional[object] = None) -> None:
        self.bus_num = bus_num
        self._lock = threading.RLock()
        self._backend: object
        if backend is not None:
            self._backend = backend
        else:
            try:
                from smbus2 import SMBus  # type: ignore
            except ImportError as exc:
                raise SensorFault(
                    "smbus2 is not installed. On the Pi: pip install smbus2. "
                    "For local testing use --mock."
                ) from exc
            try:
                self._backend = SMBus(bus_num)
            except (OSError, IOError) as exc:
                raise SensorFault(
                    f"Cannot open /dev/i2c-{bus_num}. Enable I2C "
                    f"(sudo raspi-config nonint do_i2c 0) and check wiring."
                ) from exc

    def close(self) -> None:
        with self._lock:
            closer = getattr(self._backend, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass

    def _write_bytes(self, addr: int, data: Sequence[int]) -> None:
        payload = [b & 0xFF for b in data]
        # FakeBus / test backend
        if hasattr(self._backend, "write_bytes"):
            self._backend.write_bytes(addr, payload)  # type: ignore[attr-defined]
            return
        from smbus2 import i2c_msg  # type: ignore

        write = i2c_msg.write(addr, bytes(payload))
        self._backend.i2c_rdwr(write)  # type: ignore[attr-defined]

    def _write_read(self, addr: int, write_data: Sequence[int], read_len: int) -> List[int]:
        payload = [b & 0xFF for b in write_data]
        if hasattr(self._backend, "write_read"):
            return [b & 0xFF for b in self._backend.write_read(addr, payload, read_len)]  # type: ignore[attr-defined]
        from smbus2 import i2c_msg  # type: ignore

        write = i2c_msg.write(addr, bytes(payload))
        read = i2c_msg.read(addr, read_len)
        self._backend.i2c_rdwr(write, read)  # type: ignore[attr-defined]
        return [b & 0xFF for b in list(read)]

    def write_u8(self, addr: int, reg16: int, value: int) -> None:
        """Write one byte to a 16-bit register address (VL6180X style)."""
        with self._lock:
            try:
                self._write_bytes(addr, [(reg16 >> 8) & 0xFF, reg16 & 0xFF, value & 0xFF])
            except SensorFault:
                raise
            except Exception as exc:
                raise SensorFault(
                    f"I2C write_u8 failed addr=0x{addr:02X} reg=0x{reg16:04X}: {exc}"
                ) from exc

    def write_u16(self, addr: int, reg16: int, value: int) -> None:
        """Write a big-endian 16-bit value to a 16-bit register address."""
        with self._lock:
            try:
                self._write_bytes(
                    addr,
                    [
                        (reg16 >> 8) & 0xFF,
                        reg16 & 0xFF,
                        (value >> 8) & 0xFF,
                        value & 0xFF,
                    ],
                )
            except SensorFault:
                raise
            except Exception as exc:
                raise SensorFault(
                    f"I2C write_u16 failed addr=0x{addr:02X} reg=0x{reg16:04X}: {exc}"
                ) from exc

    def read_u8(self, addr: int, reg16: int) -> int:
        """Read one byte from a 16-bit register address (combined transaction)."""
        with self._lock:
            try:
                data = self._write_read(addr, [(reg16 >> 8) & 0xFF, reg16 & 0xFF], 1)
                return data[0] & 0xFF
            except SensorFault:
                raise
            except Exception as exc:
                raise SensorFault(
                    f"I2C read_u8 failed addr=0x{addr:02X} reg=0x{reg16:04X}: {exc}"
                ) from exc

    def read_u16(self, addr: int, reg16: int) -> int:
        """Read a big-endian 16-bit value from a 16-bit register address."""
        data = self.read_block(addr, reg16, 2)
        return ((data[0] & 0xFF) << 8) | (data[1] & 0xFF)

    def read_block(self, addr: int, reg16: int, length: int) -> List[int]:
        """Write a 16-bit register pointer, then read `length` bytes."""
        with self._lock:
            try:
                return self._write_read(addr, [(reg16 >> 8) & 0xFF, reg16 & 0xFF], length)
            except SensorFault:
                raise
            except Exception as exc:
                raise SensorFault(
                    f"I2C read_block failed addr=0x{addr:02X} reg=0x{reg16:04X}: {exc}"
                ) from exc

    def write_raw(self, addr: int, data: Sequence[int]) -> None:
        """Write a raw byte sequence with no register pointer (SCD4x commands)."""
        with self._lock:
            try:
                self._write_bytes(addr, data)
            except SensorFault:
                raise
            except Exception as exc:
                raise SensorFault(f"I2C write_raw failed addr=0x{addr:02X}: {exc}") from exc

    def write_then_read(self, addr: int, write_data: Sequence[int], read_len: int) -> List[int]:
        """Combined write-then-read transaction (SCD4x command + response)."""
        with self._lock:
            try:
                return self._write_read(addr, write_data, read_len)
            except SensorFault:
                raise
            except Exception as exc:
                raise SensorFault(f"I2C write_then_read failed addr=0x{addr:02X}: {exc}") from exc

    def probe(self, addr: int) -> bool:
        """Return True if a device ACKs at `addr`."""
        with self._lock:
            try:
                if hasattr(self._backend, "probe"):
                    return bool(self._backend.probe(addr))  # type: ignore[attr-defined]
                self._write_bytes(addr, [])
                return True
            except Exception:
                return False

    def scan(self, start: int = 0x08, end: int = 0x77) -> List[int]:
        found: List[int] = []
        for addr in range(start, end + 1):
            if self.probe(addr):
                found.append(addr)
            time.sleep(0.001)
        return found


class FakeBus:
    """In-memory I2C backend for unit tests.

    Devices register handlers:
      set_device(addr, regs=dict) for VL6180X-style 16-bit registers
      set_command_device(addr, handler) for SCD4x-style command streams
    """

    def __init__(self) -> None:
        self.devices: dict = {}
        self.command_devices: dict = {}
        self.writes: list = []
        self.fail_addrs: set = set()

    def set_device(self, addr: int, regs: Optional[dict] = None) -> dict:
        store = regs if regs is not None else {}
        self.devices[addr] = store
        return store

    def set_command_device(self, addr: int, handler) -> None:
        self.command_devices[addr] = handler

    def fail(self, addr: int, on: bool = True) -> None:
        if on:
            self.fail_addrs.add(addr)
        else:
            self.fail_addrs.discard(addr)

    def probe(self, addr: int) -> bool:
        return addr in self.devices or addr in self.command_devices

    def close(self) -> None:
        pass

    def write_bytes(self, addr: int, data: Sequence[int]) -> None:
        if addr in self.fail_addrs:
            raise OSError(f"fake NACK addr=0x{addr:02X}")
        payload = [b & 0xFF for b in data]
        self.writes.append(("w", addr, payload))
        if addr in self.command_devices:
            self.command_devices[addr]("write", payload, None)
            return
        if addr not in self.devices:
            raise OSError(f"no device at 0x{addr:02X}")
        if len(payload) >= 3:
            reg = (payload[0] << 8) | payload[1]
            if len(payload) == 3:
                self.devices[addr][reg] = payload[2]
            elif len(payload) == 4:
                self.devices[addr][reg] = (payload[2] << 8) | payload[3]
            else:
                self.devices[addr][reg] = list(payload[2:])

    def write_read(self, addr: int, write_data: Sequence[int], read_len: int) -> List[int]:
        if addr in self.fail_addrs:
            raise OSError(f"fake NACK addr=0x{addr:02X}")
        payload = [b & 0xFF for b in write_data]
        self.writes.append(("wr", addr, payload, read_len))
        if addr in self.command_devices:
            return list(self.command_devices[addr]("write_read", payload, read_len))
        if addr not in self.devices:
            raise OSError(f"no device at 0x{addr:02X}")
        if len(payload) < 2:
            raise OSError("expected 16-bit register pointer")
        reg = (payload[0] << 8) | payload[1]
        val = self.devices[addr].get(reg, 0)
        if isinstance(val, list):
            out = list(val)
        elif read_len == 1:
            out = [val & 0xFF]
        elif read_len == 2:
            out = [(val >> 8) & 0xFF, val & 0xFF]
        else:
            out = [val & 0xFF] + [0] * (read_len - 1)
        while len(out) < read_len:
            out.append(0)
        return out[:read_len]
