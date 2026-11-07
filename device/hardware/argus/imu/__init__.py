"""MPU-6050 FIFO driver (spec §2.6, §4.2). Register map: InvenSense MPU-6000/6050 Register Map RM-MPU-6000A.

Hat frame: x forward, y left, z up. `axis_map` option maps sensor axes to hat axes; default identity
(sensor X/Y/Z == hat x/y/z). Example for a board mounted rotated 90 deg about z:
    axis_map: {x: "+y", y: "-x", z: "+z"}   # hat x = +sensor Y, hat y = -sensor X, hat z = +sensor Z
"""

from __future__ import annotations

import struct
import threading
import time
from collections import deque
from collections.abc import Callable

from argus.contracts import Health, ImuSample, ModuleContext

from .._common import HealthTracker, I2CBus, SmbusI2C, StoppableThread

# --- register addresses (RM-MPU-6000A) ---
SMPLRT_DIV = 0x19
CONFIG = 0x1A  # bits 2:0 DLPF_CFG
GYRO_CONFIG = 0x1B  # bits 4:3 FS_SEL
ACCEL_CONFIG = 0x1C  # bits 4:3 AFS_SEL
FIFO_EN = 0x23  # 0x40/0x20/0x10 = XG/YG/ZG, 0x08 = ACCEL, 0x80 = TEMP
INT_ENABLE = 0x38
INT_STATUS = 0x3A  # bit 4 FIFO_OFLOW_INT
SIGNAL_PATH_RESET = 0x68
USER_CTRL = 0x6A  # bit 6 FIFO_EN, bit 2 FIFO_RESET
PWR_MGMT_1 = 0x6B  # bit 7 DEVICE_RESET, bits 2:0 CLKSEL (1 = PLL with X gyro)
FIFO_COUNT_H = 0x72
FIFO_R_W = 0x74
WHO_AM_I = 0x75  # reads 0x68

FIFO_SIZE = 1024
SAMPLE_BYTES = 12  # ax ay az gx gy gz, int16 big-endian each (FIFO order: accel then gyro)
ACCEL_LSB_PER_G = 2048.0  # AFS_SEL=3 (+-16 g)
GYRO_LSB_PER_DPS = 32.8  # FS_SEL=2 (+-1000 deg/s)
_SAMPLE = struct.Struct(">6h")


def parse_axis_map(spec: dict | None) -> tuple[tuple[int, float], ...]:
    """-> for hat x,y,z: (sensor_axis_index, sign)."""
    spec = spec or {"x": "+x", "y": "+y", "z": "+z"}
    idx = {"x": 0, "y": 1, "z": 2}
    out = []
    for hat in ("x", "y", "z"):
        s = str(spec[hat]).strip().lower()
        sign = -1.0 if s.startswith("-") else 1.0
        axis = s.lstrip("+-")
        if axis not in idx:
            raise ValueError(f"bad axis_map entry {hat}: {spec[hat]!r}")
        out.append((idx[axis], sign))
    if len({a for a, _ in out}) != 3:
        raise ValueError("axis_map must use each sensor axis exactly once")
    return tuple(out)


def decode_fifo(data: bytes, axis_map) -> list[tuple[float, ...]]:
    """Big-endian signed FIFO bytes -> [(ax,ay,az,gx,gy,gz)] in g, deg/s, hat frame."""
    out = []
    for off in range(0, len(data) - SAMPLE_BYTES + 1, SAMPLE_BYTES):
        raw = _SAMPLE.unpack_from(data, off)
        acc = [v / ACCEL_LSB_PER_G for v in raw[:3]]
        gyr = [v / GYRO_LSB_PER_DPS for v in raw[3:]]
        out.append(tuple(acc[i] * s for i, s in axis_map) + tuple(gyr[i] * s for i, s in axis_map))
    return out


class Mpu6050Imu:
    def __init__(
        self,
        ctx: ModuleContext,
        bus: I2CBus | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        o = ctx.options
        self._clock = ctx.clock
        self._bus = bus
        self._own_bus = bus is None
        self._sleep = sleep
        self._i2c_bus_no = int(o.get("i2c_bus", 1))
        self._addr = int(o.get("address", 0x68))
        self._rate = float(o.get("sample_rate_hz", 1000))
        self._dlpf = int(o.get("dlpf_cfg", 0))
        self._drain_s = min(float(o.get("drain_interval_ms", 20)), 40.0) / 1000.0  # spec: <= 40 ms
        self._chunk = max(1, int(o.get("read_chunk_samples", 20))) * SAMPLE_BYTES
        self._axis_map = parse_axis_map(o.get("axis_map"))
        self._q: deque[ImuSample] = deque(maxlen=int(o.get("queue_max", 8192)))
        self._qlock = threading.Lock()
        self._health = HealthTracker(ctx.clock, stale_ms=float(o.get("stale_ms", 500)))
        self._thread: StoppableThread | None = None
        self.dropped = 0
        # Gyro output rate is 8 kHz when DLPF is disabled (CFG 0 or 7), else 1 kHz (reg map, CONFIG/SMPLRT_DIV).
        gyro_rate = 8000.0 if self._dlpf in (0, 7) else 1000.0
        self._div = max(0, min(255, round(gyro_rate / self._rate) - 1))
        self.actual_rate_hz = gyro_rate / (1 + self._div)
        self._period_ns = 1e9 / self.actual_rate_hz

    # ---- setup
    def configure(self) -> None:
        b, a = self._bus, self._addr
        b.write_byte(a, PWR_MGMT_1, 0x80)  # DEVICE_RESET
        self._sleep(0.1)
        b.write_byte(a, PWR_MGMT_1, 0x01)  # wake (SLEEP=0), CLKSEL=1 PLL X-gyro
        self._sleep(0.01)
        b.write_byte(a, SIGNAL_PATH_RESET, 0x07)
        b.write_byte(a, SMPLRT_DIV, self._div)
        b.write_byte(a, CONFIG, self._dlpf & 0x07)
        b.write_byte(a, GYRO_CONFIG, 0x02 << 3)  # +-1000 deg/s
        b.write_byte(a, ACCEL_CONFIG, 0x03 << 3)  # +-16 g
        b.write_byte(a, INT_ENABLE, 0x00)
        b.write_byte(a, USER_CTRL, 0x00)
        b.write_byte(a, USER_CTRL, 0x04)  # FIFO_RESET
        b.write_byte(a, USER_CTRL, 0x40)  # FIFO_EN
        b.write_byte(a, FIFO_EN, 0x78)  # XG|YG|ZG|ACCEL (no temp) -> 12 bytes/sample

    def _fifo_count(self) -> int:
        d = self._bus.read_block(self._addr, FIFO_COUNT_H, 2)
        return (d[0] << 8) | d[1]

    def _reset_fifo(self) -> None:
        self._bus.write_byte(self._addr, USER_CTRL, 0x40 | 0x04)  # FIFO_EN + FIFO_RESET

    def drain_once(self) -> int:
        """Read everything in the FIFO, queue samples. Returns number of samples queued."""
        count = self._fifo_count()
        if count >= FIFO_SIZE:
            self._reset_fifo()
            self._health.error(f"IMU FIFO overflow (count={count}); FIFO reset, samples lost")
            return 0
        n_bytes = (count // SAMPLE_BYTES) * SAMPLE_BYTES  # leave a partial sample in place (stays aligned)
        if n_bytes == 0:
            return 0
        chunks = []
        remaining = n_bytes
        while remaining > 0:
            n = min(self._chunk, remaining)
            chunks.append(self._bus.read_block(self._addr, FIFO_R_W, n))
            remaining -= n
        t_drain = self._clock.now_ns()
        rows = decode_fifo(b"".join(chunks), self._axis_map)
        n = len(rows)
        # Newest sample ~ drain time; earlier samples back-dated by the sample period.
        samples = [ImuSample(int(t_drain - (n - 1 - i) * self._period_ns), *r) for i, r in enumerate(rows)]
        with self._qlock:
            over = len(self._q) + n - (self._q.maxlen or 0)
            if over > 0:
                self.dropped += over
            self._q.extend(samples)
        self._health.sample(t_drain)
        return n

    def _loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.drain_once()
            except Exception as e:
                self._health.error(f"IMU I2C error: {e}")
            stop.wait(self._drain_s)

    # ---- contract
    def start(self) -> None:
        if self._thread is not None:
            return
        self._health.mark_started()
        try:
            if self._bus is None:
                self._bus = SmbusI2C(self._i2c_bus_no)
            who = self._bus.read_byte(self._addr, WHO_AM_I)
            if who != 0x68:
                self._health.error(f"MPU WHO_AM_I=0x{who:02x}, expected 0x68")
            self.configure()
        except Exception as e:
            self._health.error(f"IMU init failed: {e}")
            return
        self._thread = StoppableThread("argus-imu", self._loop)
        self._thread.start()

    def stop(self) -> None:
        if self._thread:
            self._thread.stop()
            self._thread = None
        if self._bus is not None and self._own_bus:
            try:
                self._bus.write_byte(self._addr, PWR_MGMT_1, 0x40)  # sleep
                self._bus.close()
            except Exception:
                pass
            self._bus = None

    def read(self) -> list[ImuSample]:
        with self._qlock:
            out = list(self._q)
            self._q.clear()
        return out

    def health(self) -> Health:
        return self._health.snapshot(running=self._thread is not None and self._thread.alive)


def create(ctx: ModuleContext) -> Mpu6050Imu:
    return Mpu6050Imu(ctx)
