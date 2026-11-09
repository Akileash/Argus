"""
Argus on-hat runtime: dual rear HC-SR04 + Pololu MinIMU-9 v3 + dual DRV2605L haptics.

Usage (from ~/argus):
  python3 -m hat.main
  python3 -m hat.main --dry-run
  python3 -m hat.main --until-fall   # US+buzz+IMU+cam, exit after first fall clip
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

# Force gpiozero → lgpio before any pin objects are created.
from gpiozero import Device
from gpiozero.pins.lgpio import LGPIOFactory

Device.pin_factory = LGPIOFactory()

from hat import config as cfg
from hat import telemetry
from hat.camera import ClipRecorder
from hat.events import build_event, emit
from hat.haptics import Haptics
from hat.imu import FallImpactDetector
from hat.minimu9 import MiniIMU9
from hat.tof import TofWearMonitor
from hat.ultrasonic import ApproachDetector, HCSR04, MotionState
from hat.wear_state import WearState


def on_event(event: Dict[str, Any]) -> None:
    """
    Hook for the future upload / store-and-forward queue.
    Currently a no-op — emit() already prints + appends events.jsonl.
    Camera clip capture is kicked off from HatRuntime._handle.
    """
    pass


class HatRuntime:
    def __init__(self, dry_run: bool = False, until_fall: bool = False):
        self.dry_run = dry_run
        self.until_fall = until_fall
        self._stop = threading.Event()
        self._last_err = 0.0
        self.last_fall_clip: Optional[Path] = None

        self.wear_state = WearState(history_s=max(8.0, cfg.WEAR_GATE_LOOKBACK_S + 2.0))
        self.detector = FallImpactDetector(wear_state=self.wear_state)
        self.haptics = Haptics(
            dry_run=dry_run,
            on_buzz=self.detector.note_buzz,
        )
        self.camera = ClipRecorder()
        self.tof: Optional[TofWearMonitor] = None

        self.imu: Optional[MiniIMU9] = None
        self.us_left: Optional[HCSR04] = None
        self.us_right: Optional[HCSR04] = None
        self.approach_left = ApproachDetector("left")
        self.approach_right = ApproachDetector("right")
        # Read-only wearer-motion summary (gyro rate, body-motion RMS) for the US detectors.
        self.motion = MotionState()

        self._threads: list[threading.Thread] = []
        # Live view + RAM recording (hat/telemetry.py). Hooks never block or raise.
        self.tele = telemetry.create(self)

    def _log_err(self, msg: str) -> None:
        now = time.perf_counter()
        if now - self._last_err >= 1.0:
            print(f"[error] {msg}", file=sys.stderr, flush=True)
            self._last_err = now

    def _handle(self, event: Dict[str, Any], buzz_side: Optional[str] = None) -> None:
        # Buzz FIRST so JSON/disk I/O never delays the motors.
        if buzz_side:
            side = "both" if cfg.HAPTIC_WARN_BOTH else buzz_side
            self.haptics.warn(side)

        # Telemetry (after the buzz; queue.put_nowait only)
        try:
            et = event.get("type")
            if et == "line_of_fire":
                s = event.get("sensor", {})
                self.tele.event("near_miss", event.get("side"),
                                f"{s.get('distance_m')}m {s.get('closing_speed_mps')}m/s {s.get('trigger_reason', '')}")
                if buzz_side:
                    self.tele.event("buzz", buzz_side, s.get("trigger_reason", "approach"))
            elif et == "fall":
                self.tele.event("fall", None, f"peak {event.get('sensor', {}).get('peak_accel_g')}g")
        except Exception:
            pass

        # Fall → Pi Cam clip (pre + post)
        clip_types = getattr(cfg, "CAM_CLIP_TYPES", ("fall",))
        is_clip_type = event.get("type") in clip_types
        if is_clip_type:
            event["clip_pending"] = True
            if self.until_fall:
                # Block so the MP4 is finished before we shut down
                path = self.camera.capture(event["event_id"])
                self.last_fall_clip = path
                event["clip_path"] = str(path) if path else None
                event["clip_pending"] = False
                emit(event)
                on_event(event)
                print(
                    f"\n*** {str(event.get('type')).upper()} — "
                    "stopping after clip save ***",
                    flush=True,
                )
                self._stop.set()
                return
            self.camera.capture_async(event["event_id"])

        emit(event)
        on_event(event)

    def start(self) -> None:
        self.imu = MiniIMU9()
        print(f"IMU WHO_AM_I {self.imu.who_am_i()}  dry_run={self.dry_run}")

        # Start rolling camera buffer before sensor threads
        self.camera.start()

        if cfg.US_ENABLE_LEFT:
            self.us_left = HCSR04(cfg.US_LEFT_TRIG, cfg.US_LEFT_ECHO, name="left")
        if cfg.US_ENABLE_RIGHT:
            self.us_right = HCSR04(cfg.US_RIGHT_TRIG, cfg.US_RIGHT_ECHO, name="right")
        if self.us_left is None and self.us_right is None:
            raise RuntimeError("No ultrasonic sensors enabled in config.py")

        if cfg.TOF_ENABLE:
            self.tof = TofWearMonitor(self.wear_state)
            if not self.tof.start():
                print(
                    "[tof] unavailable — fall wear-gate will suppress until ToF is up",
                    flush=True,
                )

        self._threads = [
            threading.Thread(target=self._imu_loop, name="imu", daemon=True),
            threading.Thread(target=self._us_loop, name="ultrasonic", daemon=True),
        ]
        if self.tof is not None:
            self._threads.append(
                threading.Thread(target=self._tof_loop, name="tof-wear", daemon=True)
            )
        for t in self._threads:
            t.start()
        self.tele.start()
        us_bits = []
        if cfg.US_ENABLE_LEFT:
            us_bits.append("LEFT")
        if cfg.US_ENABLE_RIGHT:
            us_bits.append("RIGHT")
        buzz = "BOTH motors" if cfg.HAPTIC_WARN_BOTH else "matching side only"
        per_hz = 1.0 / (cfg.US_PING_GAP_S * max(1, len(us_bits)))
        print(
            f"Argus hat running. Ultrasonic: {'+'.join(us_bits)} → buzz {buzz}. "
            f"Fall gated on ToF wear (lookback {cfg.WEAR_GATE_LOOKBACK_S:.0f}s)."
        )
        stop_hint = (
            "Stops automatically on first FALL (after camera clip)."
            if self.until_fall
            else "Ctrl+C to stop."
        )
        print(
            f"Sonar chirp every {cfg.US_PING_GAP_S*1000:.0f} ms "
            f"(~{per_hz:.0f} Hz per sensor). "
            f"Buzz cooldown {cfg.US_COOLDOWN_S:.1f} s after a warn. "
            f"{stop_hint}"
        )
        if self.until_fall:
            print(
                f"Wait ~{cfg.CAM_PRE_S:.0f}s for the camera buffer to fill, "
                "then trigger a fall.",
                flush=True,
            )

    def _imu_loop(self) -> None:
        assert self.imu is not None
        period = 1.0 / cfg.IMU_SAMPLE_HZ
        next_t = time.perf_counter()
        last_cfg_check = next_t
        while not self._stop.is_set():
            now = time.perf_counter()
            try:
                # Cheap periodic read-back: chip may silently reset to ±2 g
                # (readings 8x too big). On loss: re-init + drop recent data.
                if now - last_cfg_check >= cfg.IMU_CONFIG_CHECK_S:
                    last_cfg_check = now
                    if not self.imu.check_config():
                        self.detector.config_lost(now)
                        self.tele.event("config_lost", None, f"loss #{self.imu.config_losses}")
                        continue
                sample = self.imu.read(now)
                self.tele.imu(sample)
                self.motion.push(now, sample.ax, sample.ay, sample.az,
                                 sample.gx, sample.gy, sample.gz)
                det = self.detector.push(sample)
                if det is not None and not self.imu.check_config():
                    # Config was wrong while this event was being built.
                    self.detector.config_lost(now)
                    self.tele.event("config_lost", None, "fall discarded")
                    print("[imu] fall discarded: sensor config was lost", flush=True)
                    det = None
                if det is not None:
                    if det.suppressed:
                        self.tele.event("fall_suppressed", None, det.suppress_reason)
                        print(
                            f"*** SUPPRESSED {det.type.upper()} "
                            f"(wear gate: {det.suppress_reason}) "
                            f"peak={det.peak_accel_g:.2f}g "
                            f"ff={det.freefall_ms:.0f}ms "
                            f"worn_at_onset={det.helmet_worn_at_onset} "
                            f"on_frac={det.wear_on_fraction:.2f}",
                            flush=True,
                        )
                        continue
                    event = build_event(
                        det.type,
                        side=None,
                        peak_accel_g=round(det.peak_accel_g, 3),
                        freefall_ms=round(det.freefall_ms, 1),
                        imu_trace=det.imu_trace,
                        confidence=det.confidence,
                        helmet_worn_at_onset=det.helmet_worn_at_onset,
                        helmet_worn_lookback_s=det.helmet_worn_lookback_s,
                        wear_on_fraction=det.wear_on_fraction,
                    )
                    # Stamp the event with the fall onset (not the later moment of
                    # confirmation): the bridge links HELMET OFF to a fall only if
                    # the hat left within [-0.1 s, +2 s] of the fall timestamp.
                    event["timestamp"] = (
                        datetime.now().astimezone() - timedelta(seconds=det.onset_age_s)
                    ).isoformat(timespec="milliseconds")
                    # Fall: log only, no haptic (per spec)
                    self._handle(event, buzz_side=None)
            except OSError as e:
                self.tele.i2c_error()
                self._log_err(f"IMU I2C: {e}")

            next_t += period
            sleep = next_t - time.perf_counter()
            if sleep > 0:
                time.sleep(sleep)
            else:
                next_t = time.perf_counter()

    def _tof_loop(self) -> None:
        assert self.tof is not None
        last_worn = False
        while not self._stop.is_set():
            try:
                snap = self.tof.poll_once()
                self.tele.wear(snap)
                if snap.changed or snap.helmet_worn != last_worn:
                    print(
                        f"[tof] HELMET {'ON' if snap.helmet_worn else 'OFF'}  "
                        f"mm={snap.distance_mm} near={snap.near}",
                        flush=True,
                    )
                    last_worn = snap.helmet_worn
            except Exception as e:
                self.tele.i2c_error()
                self._log_err(f"ToF: {e}")
            time.sleep(cfg.TOF_POLL_GAP_S)

    def _us_loop(self) -> None:
        """Ping enabled sensors; if both, alternate ~40 ms apart."""
        sensors = []
        if self.us_left is not None:
            sensors.append((self.us_left, self.approach_left))
        if self.us_right is not None:
            sensors.append((self.us_right, self.approach_right))
        if not sensors:
            return

        # Optional raw recording for tuning: ARGUS_US_RECORD=/path/us.csv  (t,side,raw_m,gyro_dps,dyn_rms_g,moving)
        rec_path = os.environ.get("ARGUS_US_RECORD")
        rec = open(rec_path, "a", buffering=1) if rec_path else None
        if rec is not None and rec.tell() == 0:
            rec.write("t,side,raw_m,gyro_dps,dyn_rms_g,moving\n")

        idx = 0
        while not self._stop.is_set():
            t0 = time.perf_counter()
            sensor, detector = sensors[idx]
            if len(sensors) > 1:
                idx = (idx + 1) % len(sensors)
            try:
                dist = sensor.distance_m()
                ev = detector.update(t0, dist, self.motion)
                self.tele.us(t0, sensor.name, dist, detector, self.motion)
                if rec is not None:
                    rec.write(f"{t0:.4f},{sensor.name},{'' if dist is None else f'{dist:.4f}'},"
                              f"{self.motion.gyro_dps:.1f},{self.motion.dyn_rms_g:.3f},"
                              f"{int(self.motion.moving(t0))}\n")
                if cfg.US_DEBUG:
                    d = detector.last_distance
                    d_s = f"{d:5.2f}" if d is not None else " ----"
                    c = detector.last_closing
                    c_s = f"{c:+5.2f}" if c is not None else "  ----"
                    raw_s = f"{dist:5.2f}" if dist is not None else " ----"
                    print(
                        f"\r[{sensor.name}] raw={raw_s}  "
                        f"track={d_s}  close={c_s} m/s   ",
                        end="",
                        flush=True,
                    )
                if ev is not None:
                    if cfg.US_DEBUG:
                        print()  # break the \r line before JSON
                    event = build_event(
                        "line_of_fire",
                        side=ev.side,
                        distance_m=ev.distance_m,
                        closing_speed_mps=ev.closing_speed_mps,
                    )
                    event["sensor"]["trigger_reason"] = ev.reason
                    self._handle(event, buzz_side=ev.side)
            except Exception as e:
                self._log_err(f"ultrasonic {sensor.name}: {e}")

            elapsed = time.perf_counter() - t0
            time.sleep(max(0.0, cfg.US_PING_GAP_S - elapsed))

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=2.0)
        self.tele.stop()
        self.camera.stop()
        self.haptics.close()
        if self.imu is not None:
            self.imu.close()
        if self.tof is not None:
            self.tof.close()
        for us in (self.us_left, self.us_right):
            if us is not None:
                us.close()
        print(
            f"Shutdown complete. "
            f"suppressed falls={self.detector.suppressed_falls} "
            f"impacts={self.detector.suppressed_impacts}",
            flush=True,
        )


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Argus on-hat sensing runtime")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip motors (haptics print only); sensors still run",
    )
    parser.add_argument(
        "--until-fall",
        action="store_true",
        help="Run US+buzz+IMU+camera; exit after first fall clip is saved",
    )
    args = parser.parse_args(argv)

    runtime = HatRuntime(dry_run=args.dry_run, until_fall=args.until_fall)

    def _shutdown(signum, frame) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _shutdown)

    try:
        runtime.start()
        while not runtime._stop.is_set():
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nStopping…", flush=True)
    finally:
        runtime.stop()

    if args.until_fall and runtime.last_fall_clip is not None:
        print(f"DONE  {runtime.last_fall_clip}", flush=True)
        return 0
    if args.until_fall:
        print("Stopped without a fall clip.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
