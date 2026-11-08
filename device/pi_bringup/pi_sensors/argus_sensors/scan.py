"""I2C bus scan with known-address labels."""

from __future__ import annotations

from typing import Dict, List, Tuple

from .bus import I2CBus

KNOWN: Dict[int, str] = {
    0x29: "VL6180X (ToF / wear)",
    0x62: "SCD41 (CO2 / temp / RH)",
    0x68: "MPU6050/MPU9250/DS3231 (common IMU/RTC)",
    0x5A: "DRV2605L haptic (or MLX90614)",
    0x76: "BME280/BMP280",
    0x77: "BME280/BMP280 (alt)",
    0x3C: "SSD1306 OLED",
    0x3D: "SSD1306 OLED (alt)",
    0x40: "INA219 / HTU21D / PCA9685",
    0x48: "ADS1115 / TMP102",
    0x57: "MAX30102 / EEPROM",
    0x69: "MPU6050 (AD0 high)",
}


def label(addr: int) -> str:
    return KNOWN.get(addr, "unknown")


def format_scan(addrs: List[int]) -> str:
    lines = ["     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f"]
    for row in range(0, 0x80, 0x10):
        cells = [f"{row:02x}:"]
        for col in range(0x10):
            addr = row + col
            if addr < 0x08 or addr > 0x77:
                cells.append("   ")
            elif addr in addrs:
                cells.append(f"{addr:02x}")
            else:
                cells.append("--")
        lines.append(" ".join(cells))
    lines.append("")
    if addrs:
        lines.append("Found:")
        for a in addrs:
            lines.append(f"  0x{a:02X}  {label(a)}")
    else:
        lines.append("No devices found. Check wiring / enable I2C.")
    return "\n".join(lines)


def scan_bus(bus: I2CBus) -> Tuple[List[int], str]:
    addrs = bus.scan()
    return addrs, format_scan(addrs)
