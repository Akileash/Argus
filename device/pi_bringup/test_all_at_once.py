#!/usr/bin/env python3
"""Run EVERY hat/pi_sensors device at once (laptop or battery load test).

  cd ~/argus && source .venv/bin/activate
  python test_all_at_once.py              # 30 s default
  python test_all_at_once.py --seconds 60

Starts in parallel:
  - VL6180X wear (ToF) in-process → shared WearState (fall gate)
  - SCD41 air via pi_sensors subprocess
  - MPU IMU @ IMU_SAMPLE_HZ with wear-gated FallImpactDetector
  - Dual HC-SR04 pings
  - Periodic LEFT/RIGHT haptic buzzes (ignored by fall detector)

Prints a live status line and a PASS/FAIL summary. Ctrl+C to stop early.
Camera is skipped (not part of this power/sensor bring-up).
"""

from __future__ import annotations

import argparse
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from gpiozero import Device
from gpiozero.pins.lgpio import LGPIOFactory

Device.pin_factory = LGPIOFactory()

from hat import config as cfg
from hat.events import build_event, emit
from hat.haptics import Haptics
from hat.imu import FallImpactDetector, MPU6050
from hat.tof import TofWearMonitor
from hat.ultrasonic import HCSR04
from hat.wear_state import WearState

ARGUS = Path(__file__).resolve().parent
PI_SENSORS = ARGUS / "pi_sensors"
STOP = threading.Event()


@dataclass
class Stats:
    lock: threading.Lock = field(default_factory=threading.Lock)
    imu_ok: int = 0
    imu_err: int = 0
    imu_last_g: Optional[float] = None
    imu_peak_g: float = 0.0
    imu_min_g: float = 99.0
    fall_events: int = 0
    impact_events: int = 0
    suppressed_events: int = 0
    last_imu_event: Optional[str] = None
    us_l_hit: int = 0
    us_l_miss: int = 0
    us_r_hit: int = 0
    us_r_miss: int = 0
    us_l_m: Optional[float] = None
    us_r_m: Optional[float] = None
    buzz_l_ok: int = 0
    buzz_l_err: int = 0
    buzz_r_ok: int = 0
    buzz_r_err: int = 0
    tof_lines: int = 0
    scd_lines: int = 0
    helmet_on: bool = False
    last_co2: Optional[float] = None
    child_rc: Optional[int] = None
    notes: List[str] = field(default_factory=list)


def _throttled() -> str:
    try:
        out = subprocess.check_output(["vcgencmd", "get_throttled"], text=True).strip()
        return out
    except Exception as exc:  # noqa: BLE001
        return f"unavailable ({exc})"


def run_tof_wear(stats: Stats, tof: TofWearMonitor) -> None:
    """In-process ToF → WearState (shared with fall detector)."""
    while not STOP.is_set():
        try:
            snap = tof.poll_once()
            with stats.lock:
                stats.tof_lines += 1
                stats.helmet_on = snap.helmet_worn
        except Exception as exc:  # noqa: BLE001
            with stats.lock:
                stats.notes.append(f"tof: {exc}")
        time.sleep(cfg.TOF_POLL_GAP_S)


def run_scd(stats: Stats, seconds: float) -> None:
    """pi_sensors SCD41 only (ToF is in-process for wear gate)."""
    venv_py = PI_SENSORS / ".venv" / "bin" / "python"
    if not venv_py.is_file():
        with stats.lock:
            stats.notes.append("pi_sensors .venv missing — SCD skipped")
        return
    cmd = [
        str(venv_py),
        "-m",
        "argus_sensors",
        "--sensors",
        "scd41",
        "--scd-bus",
        "1",
        "--log-dir",
        str(PI_SENSORS / "logs"),
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(PI_SENSORS),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        with stats.lock:
            stats.notes.append(f"SCD spawn failed: {exc}")
        return

    assert proc.stdout is not None
    t_end = time.monotonic() + seconds
    try:
        while time.monotonic() < t_end and not STOP.is_set():
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
                continue
            line = line.rstrip()
            with stats.lock:
                if "AIR" in line and "co2=" in line:
                    stats.scd_lines += 1
                    try:
                        part = line.split("co2=", 1)[1].split(" ", 1)[0]
                        stats.last_co2 = float(part)
                    except (IndexError, ValueError):
                        pass
    finally:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
        with stats.lock:
            stats.child_rc = proc.returncode


def run_imu(stats: Stats, det: FallImpactDetector) -> None:
    try:
        mpu = MPU6050()
    except Exception as exc:  # noqa: BLE001
        with stats.lock:
            stats.notes.append(f"IMU open failed: {exc}")
        return
    period = 1.0 / cfg.IMU_SAMPLE_HZ
    next_t = time.perf_counter()
    try:
        while not STOP.is_set():
            now = time.perf_counter()
            try:
                s = mpu.read(now)
                hit = det.push(s)
                with stats.lock:
                    stats.imu_ok += 1
                    stats.imu_last_g = s.mag
                    stats.imu_peak_g = max(stats.imu_peak_g, s.mag)
                    stats.imu_min_g = min(stats.imu_min_g, s.mag)
                if hit:
                    if hit.suppressed:
                        with stats.lock:
                            stats.suppressed_events += 1
                        print(
                            f"*** SUPPRESSED {hit.type.upper()} "
                            f"({hit.suppress_reason}) peak={hit.peak_accel_g:.2f}g "
                            f"worn={hit.helmet_worn_at_onset} "
                            f"on_frac={hit.wear_on_fraction:.2f}",
                            flush=True,
                        )
                    else:
                        emit(
                            build_event(
                                hit.type,
                                side=None,
                                peak_accel_g=round(hit.peak_accel_g, 3),
                                freefall_ms=round(hit.freefall_ms, 1),
                                imu_trace=hit.imu_trace,
                                confidence=hit.confidence,
                                helmet_worn_at_onset=hit.helmet_worn_at_onset,
                                helmet_worn_lookback_s=hit.helmet_worn_lookback_s,
                                wear_on_fraction=hit.wear_on_fraction,
                            )
                        )
                        with stats.lock:
                            stats.fall_events += 1
                            stats.last_imu_event = (
                                f"fall peak={hit.peak_accel_g:.2f}g "
                                f"ff={hit.freefall_ms:.0f}ms conf={hit.confidence}"
                            )
                        print(
                            f"*** IMU EVENT  FALL  "
                            f"peak={hit.peak_accel_g:.2f}g  "
                            f"freefall_ms={hit.freefall_ms:.0f}  "
                            f"confidence={hit.confidence}  "
                            f"worn={hit.helmet_worn_at_onset}",
                            flush=True,
                        )
            except OSError:
                with stats.lock:
                    stats.imu_err += 1
            next_t += period
            sleep = next_t - time.perf_counter()
            if sleep > 0:
                time.sleep(sleep)
            else:
                next_t = time.perf_counter()
    finally:
        mpu.close()


def run_us(stats: Stats) -> None:
    try:
        left = HCSR04(cfg.US_LEFT_TRIG, cfg.US_LEFT_ECHO, "left")
        right = HCSR04(cfg.US_RIGHT_TRIG, cfg.US_RIGHT_ECHO, "right")
    except Exception as exc:  # noqa: BLE001
        with stats.lock:
            stats.notes.append(f"US open failed: {exc}")
        return
    try:
        while not STOP.is_set():
            dl = left.distance_m()
            time.sleep(cfg.US_PING_GAP_S)
            dr = right.distance_m()
            with stats.lock:
                if dl is None:
                    stats.us_l_miss += 1
                else:
                    stats.us_l_hit += 1
                    stats.us_l_m = dl
                if dr is None:
                    stats.us_r_miss += 1
                else:
                    stats.us_r_hit += 1
                    stats.us_r_m = dr
            time.sleep(cfg.US_PING_GAP_S)
    finally:
        left.close()
        right.close()


def run_haptics(
    stats: Stats,
    det: FallImpactDetector,
    period_s: float = 4.0,
) -> None:
    try:
        h = Haptics(dry_run=False, on_buzz=det.note_buzz)
    except Exception as exc:  # noqa: BLE001
        with stats.lock:
            stats.notes.append(f"Haptics open failed: {exc}")
        return
    side = "left"
    try:
        while not STOP.is_set():
            try:
                det.note_buzz()
                h.warn(side)
                with stats.lock:
                    if side == "left":
                        stats.buzz_l_ok += 1
                    else:
                        stats.buzz_r_ok += 1
            except OSError as exc:
                with stats.lock:
                    if side == "left":
                        stats.buzz_l_err += 1
                    else:
                        stats.buzz_r_err += 1
                    stats.notes.append(f"buzz {side}: {exc}")
            side = "right" if side == "left" else "left"
            # wake often so STOP is responsive
            for _ in range(int(period_s / 0.2)):
                if STOP.is_set():
                    break
                time.sleep(0.2)
    finally:
        h.close()


def status_loop(stats: Stats, seconds: float) -> None:
    t0 = time.monotonic()
    while not STOP.is_set() and (time.monotonic() - t0) < seconds:
        with stats.lock:
            helmet = "ON " if stats.helmet_on else "OFF"
            co2 = f"{stats.last_co2:.0f}" if stats.last_co2 is not None else "----"
            g = f"{stats.imu_last_g:.2f}" if stats.imu_last_g is not None else "----"
            peak = stats.imu_peak_g
            amin = stats.imu_min_g if stats.imu_min_g < 90 else 0.0
            # reset window peaks each status tick
            stats.imu_peak_g = stats.imu_last_g or 0.0
            stats.imu_min_g = stats.imu_last_g or 99.0
            lm = f"{stats.us_l_m:.2f}" if stats.us_l_m is not None else "----"
            rm = f"{stats.us_r_m:.2f}" if stats.us_r_m is not None else "----"
            line = (
                f"t={time.monotonic()-t0:5.1f}s  "
                f"HELMET {helmet}  CO2={co2:>4}  "
                f"|a|={g:>4}g min={amin:.2f} peak={peak:.2f}  "
                f"US L={lm:>4} R={rm:>4}  "
                f"buzz L/R={stats.buzz_l_ok}/{stats.buzz_r_ok}  "
                f"falls={stats.fall_events} impacts={stats.impact_events} "
                f"supp={stats.suppressed_events}"
            )
        print(line, flush=True)
        time.sleep(1.0)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="All sensors at once — load / battery test")
    p.add_argument("--seconds", type=float, default=30.0, help="How long to run (default 30)")
    p.add_argument("--buzz-period", type=float, default=4.0, help="Seconds between L/R buzzes")
    args = p.parse_args(argv)

    def _stop(_s=None, _f=None) -> None:
        STOP.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    print("=" * 64)
    print("ALL-SENSORS-AT-ONCE load test (wear-gated falls)")
    print(f"  duration     : {args.seconds:.0f}s")
    print(f"  throttled    : {_throttled()}")
    print(f"  wear gate    : lookback={cfg.WEAR_GATE_LOOKBACK_S:.0f}s "
          f"min_on_frac={cfg.WEAR_GATE_MIN_ON_FRACTION:.0%}")
    print("  devices      : ToF(wear) + SCD41 + IMU + US L/R + haptics")
    print("  tip          : helmet ON ≥5s, then fall → *** IMU EVENT FALL")
    print("                 (no impact type — hard hits are falls too)")
    print("                 helmet OFF + shake → *** SUPPRESSED")
    print("=" * 64)

    stats = Stats()
    wear = WearState(history_s=max(8.0, cfg.WEAR_GATE_LOOKBACK_S + 2.0))
    det = FallImpactDetector(wear_state=wear)
    tof = TofWearMonitor(wear)
    tof_ok = tof.start()
    if not tof_ok:
        with stats.lock:
            stats.notes.append("ToF init failed — falls will be wear-suppressed")

    threads = [
        threading.Thread(target=run_tof_wear, args=(stats, tof), name="tof", daemon=True),
        threading.Thread(target=run_scd, args=(stats, args.seconds), name="scd", daemon=True),
        threading.Thread(target=run_imu, args=(stats, det), name="imu", daemon=True),
        threading.Thread(target=run_us, args=(stats,), name="us", daemon=True),
        threading.Thread(
            target=run_haptics,
            args=(stats, det, args.buzz_period),
            name="haptics",
            daemon=True,
        ),
        threading.Thread(target=status_loop, args=(stats, args.seconds), name="status", daemon=True),
    ]
    for t in threads:
        t.start()

    t_end = time.monotonic() + args.seconds
    while time.monotonic() < t_end and not STOP.is_set():
        time.sleep(0.2)
    STOP.set()
    for t in threads:
        t.join(timeout=8)
    tof.close()

    print("\n" + "=" * 64)
    print("SUMMARY")
    print("=" * 64)
    with stats.lock:
        print(f"  throttled end : {_throttled()}")
        print(f"  ToF samples   : {stats.tof_lines}  helmet_on={stats.helmet_on}")
        print(f"  SCD air lines : {stats.scd_lines}  last_co2={stats.last_co2}")
        print(f"  IMU           : ok={stats.imu_ok} err={stats.imu_err} last_|a|={stats.imu_last_g}")
        print(
            f"  Fall/Impact   : falls={stats.fall_events} impacts={stats.impact_events} "
            f"suppressed={stats.suppressed_events} "
            f"(det supp f/i={det.suppressed_falls}/{det.suppressed_impacts}) "
            f"last={stats.last_imu_event or '(none)'}"
        )
        l_tot = stats.us_l_hit + stats.us_l_miss
        r_tot = stats.us_r_hit + stats.us_r_miss
        l_rate = (stats.us_l_hit / l_tot) if l_tot else 0.0
        r_rate = (stats.us_r_hit / r_tot) if r_tot else 0.0
        print(
            f"  US left       : hits={stats.us_l_hit}/{l_tot} ({l_rate:.0%}) last={stats.us_l_m}"
        )
        print(
            f"  US right      : hits={stats.us_r_hit}/{r_tot} ({r_rate:.0%}) last={stats.us_r_m}"
        )
        print(
            f"  Haptics       : L ok/err={stats.buzz_l_ok}/{stats.buzz_l_err}  "
            f"R ok/err={stats.buzz_r_ok}/{stats.buzz_r_err}"
        )
        if stats.notes:
            print("  notes:")
            for n in stats.notes[-8:]:
                print(f"    - {n}")

        checks = {
            "ToF live": stats.tof_lines > 0,
            "SCD41 air": stats.scd_lines > 0,
            "IMU reads": stats.imu_ok > 10,
            "Fall detector armed": stats.imu_ok > 10,  # events optional unless you drop it
            "US left pings": l_tot > 0,
            "US right pings": r_tot > 0,
            "Haptics left": stats.buzz_l_ok > 0 and stats.buzz_l_err == 0,
            "Haptics right": stats.buzz_r_ok > 0 and stats.buzz_r_err == 0,
        }
        print("\n  RESULTS:")
        all_ok = True
        for name, ok in checks.items():
            print(f"    [{'PASS' if ok else 'FAIL'}] {name}")
            all_ok &= ok
        # undervoltage sticky bits in get_throttled
        thr = _throttled()
        undervolt = False
        if "throttled=" in thr:
            try:
                val = int(thr.split("=", 1)[1].strip(), 16)
                # bit0 under-voltage now, bit16 under-voltage occurred
                undervolt = bool(val & ((1 << 0) | (1 << 16)))
            except ValueError:
                pass
        print(
            f"    [{'WARN' if undervolt else 'PASS'}] power throttle "
            f"({'UNDERVOLT seen' if undervolt else 'no undervolt flags'})"
        )
        print("=" * 64)
        print("OVERALL:", "ALL PASS" if all_ok else "SOME FAILED")
        return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
