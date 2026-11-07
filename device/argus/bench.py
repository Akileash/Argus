"""Bench self-test used by ``argus-cli test <module>``: live readings + a checklist (spec §5.6 row 2).

Each check returns ``PASS``, ``FAIL`` or ``SKIP``. Steps that need a human (tilt the hat, hand in
front of R2, feel the buzz) run only when ``interactive``; otherwise they are ``SKIP``.
"""

from __future__ import annotations

import math
import statistics
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from argus.contracts import DEFAULT_ACTIVE_SENSORS, Clock

Printer = Callable[[str], None]
REAL_IMU_MIN_HZ = 800.0  # MPU-6050 at 1 kHz; below this the I2C bus (400 kHz?) or the drain loop is too slow


@dataclass
class Check:
    name: str
    status: str  # PASS | FAIL | SKIP
    detail: str = ""


class Bench:
    def __init__(self, module: str, inst: Any, clock: Clock, interactive: bool, out: Printer = print) -> None:
        self.module, self.inst, self.clock, self.interactive, self.out = module, inst, clock, interactive, out
        self.checks: list[Check] = []

    # ---- helpers
    def wait(self, seconds: float, on_samples: Callable[[list[Any]], None] | None = None,
             live: Callable[[list[Any]], str] | None = None) -> list[Any]:
        """Poll ``read()`` for ``seconds`` (virtual clock: instant) and return every sample seen."""
        got: list[Any] = []
        step, next_live = 0.01, 0.0
        for i in range(int(seconds / step)):
            if hasattr(self.clock, "advance"):
                self.clock.advance(step)
            else:
                time.sleep(step)
            batch = self.inst.read() if hasattr(self.inst, "read") else []  # haptics/camera have no stream
            got.extend(batch)
            if on_samples:
                on_samples(batch)
            if live and batch and i * step >= next_live:
                next_live += 0.5
                self.out("    " + live(batch))
        return got

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        c = Check(name, "PASS" if ok else "FAIL", detail)
        self.checks.append(c)
        self.out(f"  [{c.status}] {name}  {detail}")

    def human(self, name: str, prompt: str, seconds: float, judge: Callable[[list[Any]], tuple[bool, str]],
              live: Callable[[list[Any]], str] | None = None) -> None:
        if not self.interactive:
            self.checks.append(Check(name, "SKIP", "needs a human; rerun with --interactive"))
            self.out(f"  [SKIP] {name}  (needs a human: {prompt})")
            return
        self.out(f"  >> {prompt}  (starting in 3 s)")
        self.wait(3.0)
        samples = self.wait(seconds, live=live)
        ok, detail = judge(samples)
        self.add(name, ok, detail)

    def _error_count(self) -> int:
        try:
            return int(self.inst.health().error_count)
        except Exception:  # noqa: BLE001 - a module without health() just reports no errors
            return 0

    # ---- per module
    def run(self) -> list[Check]:
        self.out(f"bench self-test: {self.module}")
        self.inst.start()
        try:
            getattr(self, f"_test_{self.module}")()
        finally:
            self.inst.stop()
        bad = [c for c in self.checks if c.status == "FAIL"]
        self.out(f"result: {'FAIL' if bad else 'PASS'} ({sum(c.status == 'PASS' for c in self.checks)} passed, "
                 f"{len(bad)} failed, {sum(c.status == 'SKIP' for c in self.checks)} skipped)")
        return self.checks

    def _test_imu(self) -> None:
        def live(b: list[Any]) -> str:
            s = b[-1]
            return f"a=({s.ax:+.2f},{s.ay:+.2f},{s.az:+.2f}) g  w=({s.gx:+.1f},{s.gy:+.1f},{s.gz:+.1f}) dps"

        errors_before = self._error_count()
        s = self.wait(3.0, live=live)
        mags = [(x.ax**2 + x.ay**2 + x.az**2) ** 0.5 for x in s]
        med = statistics.median(mags) if mags else 0.0
        # Rate from the sample timestamps: the wait loop oversleeps, so its nominal 3 s is not real time.
        span_s = (s[-1].t_ns - s[0].t_ns) / 1e9 if len(s) > 1 else 0.0
        rate = (len(s) - 1) / span_s if span_s > 0 else 0.0
        # The real driver (hardware.argus.imu) must hold ~1 kHz; the mock deliberately runs at the spec's 400 Hz.
        real = type(self.inst).__module__.startswith("hardware.argus")
        want = REAL_IMU_MIN_HZ if real else 320.0
        self.add(f"rate >= {want:.0f} Hz ({'real driver, expect ~1000' if real else 'spec: 400 Hz'})",
                 rate >= want, f"{rate:.0f} Hz")
        overflows = self._error_count() - errors_before
        self.add("no FIFO overflows / driver errors", overflows == 0, f"{overflows} new errors")
        self.add("stationary |a| ~ 1 g", 0.9 <= med <= 1.1, f"median {med:.3f} g")

        def tilt(samples: list[Any]) -> tuple[bool, str]:
            ax = max((abs(x.ax) for x in samples), default=0.0)
            ay = max((abs(x.ay) for x in samples), default=0.0)
            return max(ax, ay) > 0.7, f"max |ax|={ax:.2f} |ay|={ay:.2f} g"

        self.human("tilt 90 deg: gravity moves off z", "tilt the hat 90 degrees and hold", 3.0, tilt, live)

    def _test_range(self) -> None:
        def live(b: list[Any]) -> str:
            return " ".join(f"{x.sensor_id}={'--' if x.range_mm is None else f'{x.range_mm:.0f}'}" for x in b[-5:])

        s = self.wait(3.0, live=live)
        seen = {x.sensor_id for x in s}
        active = tuple(getattr(self.inst, "sensors", DEFAULT_ACTIVE_SENSORS))
        self.add(f"all {len(active)} active sensors report", seen == set(active), f"seen {sorted(seen)}")
        # From timestamps: the wait loop oversleeps, so its nominal 3 s is not the real elapsed time.
        span_s = (s[-1].t_ns - s[0].t_ns) / 1e9 if len(s) > 1 else 0.0
        rate = (len(s) - 1) / len(active) / span_s if span_s > 0 else 0.0
        want = 1.0 / (0.03 * len(active))
        self.add(f"~{want:.1f} Hz per sensor (+-20%)", 0.8 * want <= rate <= 1.2 * want, f"{rate:.1f} Hz")

        def hand(samples: list[Any]) -> tuple[bool, str]:
            med = {sid: statistics.median([x.range_mm for x in samples if x.sensor_id == sid and x.range_mm]
                                          or [float("nan")]) for sid in active}
            near = [sid for sid, v in med.items() if not math.isnan(v) and abs(v - 500) < 80]
            return near == ["R2"], f"sensors near 500 mm: {near}"

        self.human("hand at 50 cm: only R2 reads ~500 mm", "hold a hand 50 cm in front of R2 only", 3.0, hand, live)

    def _test_hat_on(self) -> None:
        def live(b: list[Any]) -> str:
            s = b[-1]
            return f"tof={s.range_mm} mm status={s.status}"

        s = self.wait(3.0, live=live)
        self.add("~10 Hz (+-20%)", 24 <= len(s) <= 36, f"{len(s) / 3.0:.1f} Hz")
        self.add("status values valid", all(x.status in ("ok", "out_of_range", "error") for x in s))
        self.human("hat off -> out_of_range", "take the hat off and hold it in the air", 4.0,
                   lambda smp: (sum(x.status == "out_of_range" for x in smp) > len(smp) // 2, f"{len(smp)} samples"),
                   live)

    def _test_env(self) -> None:
        s = self.wait(6.0, live=lambda b: f"co2={b[-1].co2_ppm} ppm temp={b[-1].temp_c} C rh={b[-1].rh_pct} %")
        self.add("at least one sample in 6 s (0.2 Hz)", len(s) >= 1)
        if s:
            x = s[-1]
            self.add("values plausible", 300 <= (x.co2_ppm or 0) <= 10000 and -20 <= (x.temp_c or 0) <= 60
                     and 0 <= (x.rh_pct or -1) <= 100, f"{x.co2_ppm} ppm {x.temp_c} C {x.rh_pct} %")

    def _test_haptics(self) -> None:
        try:
            self.inst.set("top", 0.0)
            self.inst.set("back", 0.0)
            self.add("zero-intensity self-test", True)
        except Exception as e:  # noqa: BLE001
            self.add("zero-intensity self-test", False, str(e))
        if not self.interactive:
            self.checks.append(Check("buzz top, then back", "SKIP", "needs a human; rerun with --interactive"))
            self.out("  [SKIP] buzz top, then back  (needs a human)")
            return
        for motor in ("top", "back"):
            self.out(f"  >> buzzing {motor} motor for 1 s")
            self.inst.set(motor, 1.0)
            self.wait(1.0)
            self.inst.off_all()
            self.add(f"{motor} motor felt", input("     felt it? [y/N] ").strip().lower() == "y")

    def _test_camera(self) -> None:
        self.wait(3.0)
        h = self.inst.health()
        self.add("health ok", h.ok, h.note)
        now = self.clock.now_ns()
        out = Path(tempfile.mkdtemp(prefix="argus-bench-")) / "bench.mp4"
        try:
            info = self.inst.save_clip(now - 2_000_000_000, now, str(out))
            self.add("save_clip writes an MP4", out.exists() and info.bytes > 0,
                     f"{info.bytes} bytes, {info.duration_s:.1f} s -> {out}")
        except Exception as e:  # noqa: BLE001
            self.add("save_clip writes an MP4", False, str(e))
