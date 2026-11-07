"""L0 module contracts: the "plug shape" every hardware implementation must satisfy.

Spec: .plans/argus-mvp-spec.md §5.2. PM-owned: agents must not change this file;
report a needed change instead.

Rules for ALL implementations (argus | team | mock | replay):
- Clock: time.monotonic_ns() only (or the injected Clock for mock/replay). Never wall-clock.
- Non-blocking: read() returns within 5 ms; slow I/O runs in the module's own thread.
- read() drains everything since the last call, oldest first.
- Never raise out of read()/set(); log, bump error_count, return what you have.
- Frame: hat frame x = forward, y = left, z = up. Accel in g, gyro in deg/s, ranges in mm.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

SensorId = Literal["R1", "R2", "R3", "R4", "TOP"]
SENSOR_IDS: tuple[SensorId, ...] = ("R1", "R2", "R3", "R4", "TOP")
# MVP fires only these (spec §3.2a). Override with modules.range.options.order; every id stays valid.
DEFAULT_ACTIVE_SENSORS: tuple[SensorId, ...] = ("R2", "R3")
Motor = Literal["top", "back"]
ModuleName = Literal["imu", "range", "hat_on", "env", "haptics", "camera"]
MODULE_NAMES: tuple[ModuleName, ...] = ("imu", "range", "hat_on", "env", "haptics", "camera")
ImplName = Literal["argus", "team", "mock", "replay"]


# ---------------------------------------------------------------- clock
class Clock(Protocol):
    def now_ns(self) -> int: ...


class MonotonicClock:
    def now_ns(self) -> int:
        return time.monotonic_ns()


# ---------------------------------------------------------------- samples
@dataclass(frozen=True, slots=True)
class ImuSample:
    t_ns: int
    ax: float  # g
    ay: float
    az: float
    gx: float  # deg/s
    gy: float
    gz: float


@dataclass(frozen=True, slots=True)
class RangeSample:
    t_ns: int
    sensor_id: SensorId
    range_mm: float | None  # None = no echo / timeout


@dataclass(frozen=True, slots=True)
class TofSample:
    t_ns: int
    range_mm: float | None
    status: Literal["ok", "out_of_range", "error"]


@dataclass(frozen=True, slots=True)
class EnvSample:
    t_ns: int
    co2_ppm: float | None
    temp_c: float | None
    rh_pct: float | None


@dataclass(frozen=True, slots=True)
class ClipInfo:
    path: str
    t_start_ns: int
    t_end_ns: int
    duration_s: float
    bytes: int
    camera: Literal["front"] = "front"


@dataclass(slots=True)
class Health:
    ok: bool
    last_sample_age_ms: float | None = None
    error_count: int = 0
    note: str = ""


# ---------------------------------------------------------------- interfaces
@runtime_checkable
class Module(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def health(self) -> Health: ...


@runtime_checkable
class Imu(Module, Protocol):
    """>= 400 Hz for impact capture (spec §4.2). ±16 g, ±1000 deg/s."""

    def read(self) -> list[ImuSample]: ...


@runtime_checkable
class RangeArray(Module, Protocol):
    """Interleaved HC-SR04 sweep, one sample per sensor per sweep (~150 ms)."""

    def read(self) -> list[RangeSample]: ...


@runtime_checkable
class HatOnSensor(Module, Protocol):
    """VL53L0X in the crown."""

    def read(self) -> list[TofSample]: ...


@runtime_checkable
class EnvSensor(Module, Protocol):
    """SCD41, ~0.2 Hz. Context only."""

    def read(self) -> list[EnvSample]: ...


@runtime_checkable
class HapticDriver(Module, Protocol):
    """Patterns live in L3 (argus.events.haptics), NOT in the driver."""

    def set(self, motor: Motor, intensity: float) -> None:  # 0.0..1.0
        ...

    def off_all(self) -> None: ...


@runtime_checkable
class Camera(Module, Protocol):
    """Keeps its own ring buffer (>= 30 s); frames stamped with the shared clock."""

    def save_clip(self, t_start_ns: int, t_end_ns: int, path: str) -> ClipInfo: ...


PROTOCOL_FOR: dict[ModuleName, type] = {
    "imu": Imu,
    "range": RangeArray,
    "hat_on": HatOnSensor,
    "env": EnvSensor,
    "haptics": HapticDriver,
    "camera": Camera,
}

# Staleness thresholds used by the supervisor for automatic fallback (spec §5.4).
STALE_AFTER_MS: dict[ModuleName, float | None] = {
    "imu": 500,
    "range": 500,
    "hat_on": 3000,
    "env": 30000,
    "haptics": None,  # checked by zero-intensity self-test every 1 s
    "camera": 2000,
}


# ---------------------------------------------------------------- factory signature
@dataclass(slots=True)
class ModuleContext:
    """Passed to every implementation's factory: `create(ctx) -> <Protocol>`.

    Each impl lives at hardware/<impl>/<module>/__init__.py and exposes
    `def create(ctx: ModuleContext) -> <Protocol>`.
    """

    clock: Clock
    options: dict = field(default_factory=dict)  # modules.<name>.options from config.yaml
    scenario: str | None = None  # mock/replay only
