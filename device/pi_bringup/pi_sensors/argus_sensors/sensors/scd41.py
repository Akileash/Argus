"""SCD41 CO2 / temperature / humidity driver.

Ported from SCD41/read_air/read_air.ino + Sensirion SCD4x datasheet commands.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from ..base import Sensor
from ..bus import I2CBus, SensorFault
from .. import events
from ..sinks import Sink

DEFAULT_ADDR = 0x62

CMD_START_PERIODIC = 0x21B1
CMD_READ_MEASUREMENT = 0xEC05
CMD_STOP_PERIODIC = 0x3F86
CMD_GET_DATA_READY = 0xE4B8

HEARTBEAT_MS = 2000
POLL_MS = 200
FAILS_BEFORE_RECOVER = 8
RECOVER_COOLDOWN_MS = 5000
STOP_WAIT_S = 0.5


def crc8(data: bytes) -> int:
    """Sensirion CRC-8: poly 0x31, init 0xFF."""
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x31) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def check_crc_word(msb: int, lsb: int, crc: int) -> bool:
    return crc8(bytes([msb & 0xFF, lsb & 0xFF])) == (crc & 0xFF)


def word_from(msb: int, lsb: int) -> int:
    return ((msb & 0xFF) << 8) | (lsb & 0xFF)


def temp_from_raw(raw: int) -> float:
    return -45.0 + 175.0 * raw / 65535.0


def rh_from_raw(raw: int) -> float:
    return 100.0 * raw / 65535.0


def encode_cmd(cmd: int) -> List[int]:
    return [(cmd >> 8) & 0xFF, cmd & 0xFF]


class SCD41:
    """Low-level SCD4x command driver."""

    def __init__(self, bus: I2CBus, address: int = DEFAULT_ADDR) -> None:
        self.bus = bus
        self.address = address

    def _send(self, cmd: int) -> None:
        self.bus.write_raw(self.address, encode_cmd(cmd))

    def stop_periodic_measurement(self) -> None:
        self._send(CMD_STOP_PERIODIC)
        time.sleep(STOP_WAIT_S)

    def start_periodic_measurement(self) -> None:
        self._send(CMD_START_PERIODIC)

    def get_data_ready(self) -> bool:
        time.sleep(0.001)
        data = self.bus.write_then_read(self.address, encode_cmd(CMD_GET_DATA_READY), 3)
        if len(data) < 3 or not check_crc_word(data[0], data[1], data[2]):
            raise SensorFault("SCD41 data-ready CRC failed")
        word = word_from(data[0], data[1])
        return (word & 0x07FF) != 0

    def read_measurement(self) -> Tuple[int, float, float]:
        time.sleep(0.001)
        data = self.bus.write_then_read(self.address, encode_cmd(CMD_READ_MEASUREMENT), 9)
        if len(data) < 9:
            raise SensorFault("SCD41 read_measurement short response")
        for i in (0, 3, 6):
            if not check_crc_word(data[i], data[i + 1], data[i + 2]):
                raise SensorFault(f"SCD41 measurement CRC failed at word {i // 3}")
        co2 = word_from(data[0], data[1])
        temp = temp_from_raw(word_from(data[3], data[4]))
        rh = rh_from_raw(word_from(data[6], data[7]))
        return co2, temp, rh


class SCD41Sensor(Sensor):
    name = "scd41"
    address = DEFAULT_ADDR
    heartbeat_ms = HEARTBEAT_MS
    poll_gap_ms = float(POLL_MS)
    fails_before_recover = FAILS_BEFORE_RECOVER
    recover_cooldown_ms = RECOVER_COOLDOWN_MS

    def __init__(self, bus: I2CBus, sink: Sink, address: int = DEFAULT_ADDR) -> None:
        super().__init__(bus, sink)
        self.address = address
        self.driver = SCD41(bus, address)
        self.last_valid = False
        self.last_co2: Optional[int] = None
        self.last_temp: Optional[float] = None
        self.last_rh: Optional[float] = None
        self.read_count = 0
        self._last_poll_mono = 0.0

    def init_sensor(self) -> bool:
        try:
            time.sleep(0.03)
            self.driver.stop_periodic_measurement()
            self.driver.start_periodic_measurement()
            return True
        except SensorFault:
            return False

    def _fields(self) -> Dict[str, Any]:
        return {
            "co2_ppm": self.last_co2 if self.last_valid else None,
            "temp_c": round(self.last_temp, 2) if self.last_valid and self.last_temp is not None else None,
            "rh_pct": round(self.last_rh, 2) if self.last_valid and self.last_rh is not None else None,
            "sensor_ok": self.sensor_ok,
            "reads": self.read_count,
            "recoveries": self.recoveries,
        }

    def make_hello(self, now: float) -> Dict[str, Any]:
        f = self._fields()
        return events.hello_scd(
            co2_ppm=f["co2_ppm"],
            temp_c=f["temp_c"],
            rh_pct=f["rh_pct"],
            sensor_ok=f["sensor_ok"],
            reads=f["reads"],
            recoveries=f["recoveries"],
            now=now,
        )

    def make_sample(self, now: float) -> Optional[Dict[str, Any]]:
        f = self._fields()
        return events.sample_scd(
            co2_ppm=f["co2_ppm"],
            temp_c=f["temp_c"],
            rh_pct=f["rh_pct"],
            sensor_ok=f["sensor_ok"],
            reads=f["reads"],
            recoveries=f["recoveries"],
            now=now,
        )

    def poll(self, now: float) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if (now - self._last_poll_mono) * 1000.0 < POLL_MS:
            return out
        self._last_poll_mono = now

        try:
            ready = self.driver.get_data_ready()
        except SensorFault:
            rec = self.note_fault(now)
            if rec:
                out.append(rec)
            return out

        self.note_ok()
        if not ready:
            return out

        try:
            co2, temp, rh = self.driver.read_measurement()
        except SensorFault:
            return out

        if co2 == 0:
            return out

        self.last_co2 = co2
        self.last_temp = temp
        self.last_rh = rh
        self.last_valid = True
        self.read_count += 1
        out.append(
            events.air_sample(
                co2_ppm=co2,
                temp_c=temp,
                rh_pct=rh,
                sensor_ok=True,
                reads=self.read_count,
                recoveries=self.recoveries,
                now=now,
            )
        )
        return out
