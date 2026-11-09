"""Live telemetry for `python -m hat.tools.live_view` plus a RAM recording for analysis.

Design rules (this runs inside hat.main next to the 200 Hz IMU loop, the ultrasonic loop and the buzz):

  * The loops only call tiny hook methods (`imu`, `us`, `wear`, `event`, ...). A hook stores a reference and does
    `queue.put_nowait` of a tuple. No formatting, no JSON, no file or socket I/O, no locks. It cannot block.
  * Every hook swallows every exception. Telemetry may fail; the sensors may not.
  * A background PUBLISHER thread builds a compact snapshot ~20 Hz and sends it as ONE UDP datagram to
    127.0.0.1 with a non-blocking socket. UDP was chosen because sendto() on loopback never blocks the sender and
    needs no listener: with nobody bound the kernel just discards the datagram (unconnected socket, so no
    ECONNREFUSED state to manage), there is no backlog to grow, no accept thread and no cleanup. A TCP/unix
    stream would need connection handling, and a file would cost writes. The viewer can start/stop any time.
    Limit: one viewer at a time can bind the port.
  * A background WRITER thread drains a bounded queue and appends CSV rows under /dev/shm (RAM, no SD wear). When
    the queue is full the new row is DROPPED and counted (`dropped`), never waited for. Files are split into
    segments and old ones are deleted by age and total size, so RAM cannot fill.

Files in LIVE_REC_DIR (one set per hat.main run, `<session>` = start time):
  imu-<session>-NNNN.csv     t,ax,ay,az,gx,gy,gz                  every IMU sample (gx..gz empty before gyro calibration)
  us-<session>-NNNN.csv      t,side,raw_m,tracked_m,closing_mps,gated,moving   every ultrasonic ping
  events-<session>-NNNN.csv  t,kind,side,text                     buzz pulses, buzz/near_miss/fall events, wear
                                                                  (ToF) rows, hat_on/hat_off, config_lost, meta
`t` is wall-clock epoch seconds (the loops' perf_counter time plus a fixed offset taken at start).
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from hat import config as cfg

_HEADERS = {
    "imu": "t,ax,ay,az,gx,gy,gz\n",
    "us": "t,side,raw_m,tracked_m,closing_mps,gated,moving\n",
    "events": "t,kind,side,text\n",
}
_STREAM_OF = {"i": "imu", "u": "us", "e": "events"}
EVENT_KEEP = 10


def _f(v: Optional[float], nd: int = 4) -> str:
    return "" if v is None else f"{v:.{nd}f}"


def _clean(s: Any) -> str:
    return str(s).replace(",", ";").replace("\n", " ").replace("\r", " ")


class _Stream:
    """One rotating CSV stream. Used only from the writer thread."""

    def __init__(self, directory: Path, name: str, session: str, segment_bytes: int) -> None:
        self.dir, self.name, self.session, self.segment_bytes = directory, name, session, segment_bytes
        self.seq = 0
        self.fh = None
        self.path: Optional[Path] = None
        self.size = 0

    def write(self, lines: List[str]) -> None:
        """Append lines, starting a new segment whenever the current one is full (even inside one batch)."""
        i = 0
        while i < len(lines):
            if self.fh is None or self.size >= self.segment_bytes:
                self._roll()
            j, room = i, self.segment_bytes - self.size
            while j < len(lines) and (j == i or room - len(lines[j]) >= 0):
                room -= len(lines[j])
                j += 1
            chunk = "".join(lines[i:j])
            self.fh.write(chunk)
            self.size += len(chunk)
            i = j

    def _roll(self) -> None:
        self.close()
        self.seq += 1
        self.path = self.dir / f"{self.name}-{self.session}-{self.seq:04d}.csv"
        self.fh = open(self.path, "a", encoding="utf-8")
        self.fh.write(_HEADERS[self.name])
        self.size = len(_HEADERS[self.name])

    def flush(self) -> None:
        if self.fh is not None:
            self.fh.flush()

    def close(self) -> None:
        if self.fh is not None:
            try:
                self.fh.close()
            except OSError:
                pass
        self.fh = None


class Recorder:
    """Bounded-queue, drop-on-full CSV writer with size/age rotation."""

    def __init__(
        self,
        directory: str | Path,
        *,
        queue_size: int = 20000,
        segment_mb: float = 4.0,
        max_mb: float = 48.0,
        keep_s: float = 1800.0,
        wall_offset: float = 0.0,
        session: Optional[str] = None,
        cleanup_every_s: float = 5.0,
    ) -> None:
        self.dir = Path(directory)
        # deque, not queue.Queue: append() takes no lock and wakes nobody, so a producer costs ~1 us and never
        # makes the writer thread wake per row (that wake-up per IMU sample cost ~250 us CPU in a measurement).
        self.q: Deque[tuple] = deque()
        self.cap = max(1, int(queue_size))
        self.max_bytes = int(max_mb * 1024 * 1024)
        self.keep_s = keep_s
        self.wall_offset = wall_offset
        self.session = session or time.strftime("%Y%m%dT%H%M%S")
        seg = int(segment_mb * 1024 * 1024)
        self._streams = {n: _Stream(self.dir, n, self.session, seg) for n in _HEADERS}
        self.dropped = 0
        self.written = 0
        self.write_errors = 0
        self._cleanup_every = cleanup_every_s
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.ok = False

    # -- producer side: never blocks, never raises
    def put(self, row: tuple) -> None:
        try:
            if len(self.q) >= self.cap:  # full: drop the NEW row, count it, never wait
                self.dropped += 1
            else:
                self.q.append(row)
        except Exception:
            self.dropped += 1

    # -- lifecycle
    def start(self) -> bool:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.cleanup()
        except OSError as e:
            print(f"[live] recording disabled: cannot use {self.dir}: {e}", flush=True)
            return False
        self.ok = True
        self._thread = threading.Thread(target=self._run, name="live-rec", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        for s in self._streams.values():
            s.close()

    # -- writer thread
    def _run(self) -> None:
        last_clean = time.monotonic()
        while not self._stop.is_set():
            self._stop.wait(0.1)  # batch: wake 10x/s, not per row
            try:
                self._drain()
                if time.monotonic() - last_clean >= self._cleanup_every:
                    last_clean = time.monotonic()
                    self.cleanup()
            except Exception:
                self.write_errors += 1
        try:  # final drain so a clean shutdown keeps the tail
            self._drain()
        except Exception:
            self.write_errors += 1

    def _drain(self, limit: int = 5000) -> None:
        rows: List[tuple] = []
        try:
            while len(rows) < limit:
                rows.append(self.q.popleft())
        except IndexError:
            pass
        if not rows:
            return
        bufs: Dict[str, List[str]] = {}
        off = self.wall_offset
        for r in rows:
            k = r[0]
            if k == "i":
                _, t, ax, ay, az, gx, gy, gz = r
                line = f"{t + off:.4f},{ax:.4f},{ay:.4f},{az:.4f},{_f(gx)},{_f(gy)},{_f(gz)}\n"
            elif k == "u":
                _, t, side, raw, trk, clo, gated, moving = r
                line = f"{t + off:.4f},{side},{_f(raw)},{_f(trk)},{_f(clo, 3)},{int(gated)},{int(moving)}\n"
            else:
                _, t, kind, side, text = r
                line = f"{t + off:.4f},{_clean(kind)},{_clean(side or '')},{_clean(text)}\n"
            bufs.setdefault(_STREAM_OF[k], []).append(line)
        for name, lines in bufs.items():
            st = self._streams[name]
            try:
                st.write(lines)
                st.flush()
                self.written += len(lines)
            except OSError:  # RAM disk full / dir removed: drop these rows, reopen next time
                self.write_errors += 1
                self.dropped += len(lines)
                st.close()

    def cleanup(self) -> None:
        """Delete files older than keep_s, then oldest first until the total is under max_bytes."""
        open_paths = {s.path for s in self._streams.values() if s.fh is not None}
        now = time.time()
        files = []
        for p in self.dir.glob("*.csv"):
            try:
                st = p.stat()
            except OSError:
                continue
            files.append([p, st.st_mtime, st.st_size])
        files.sort(key=lambda x: x[1])
        keep = []
        for p, mt, sz in files:
            if p not in open_paths and now - mt > self.keep_s:
                self._rm(p)
            else:
                keep.append((p, mt, sz))
        total = sum(sz for _, _, sz in keep)
        for p, _, sz in keep:  # oldest first
            if total <= self.max_bytes:
                break
            if p in open_paths:
                continue
            self._rm(p)
            total -= sz

    @staticmethod
    def _rm(p: Path) -> None:
        try:
            p.unlink()
        except OSError:
            pass


class NullTelemetry:
    """Same hooks, all no-ops (telemetry off)."""

    enabled = False

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def imu(self, s: Any) -> None: ...
    def us(self, t: float, side: str, raw: Optional[float], det: Any, motion: Any) -> None: ...
    def wear(self, snap: Any) -> None: ...
    def event(self, kind: str, side: Optional[str] = None, text: str = "") -> None: ...
    def i2c_error(self) -> None: ...


class Telemetry(NullTelemetry):
    enabled = True

    def __init__(
        self,
        src: Any,
        *,
        udp: bool = True,
        record: bool = True,
        host: str = "127.0.0.1",
        port: int = 47321,
        hz: float = 20.0,
        rec_dir: str = "/dev/shm/argus-live",
        rec_kwargs: Optional[dict] = None,
    ) -> None:
        self.src = src
        self.host, self.port = host, port
        self.period = 1.0 / max(1.0, hz)
        self.boot = uuid.uuid4().hex[:8]
        self.t_start = time.perf_counter()
        self.wall_offset = time.time() - time.perf_counter()
        self.seq = 0
        self.i2c_errors = 0
        self.send_errors = 0
        self._imu: Any = None
        self._us: Dict[str, tuple] = {}
        self._tof: Optional[tuple] = None  # (perf t, worn, mm, near)
        self._last_worn: Optional[bool] = None
        self._events: Deque[dict] = deque(maxlen=EVENT_KEEP)
        self._ev_id = 0
        self._last_fire = {"left": 0.0, "right": 0.0}
        self._sock: Optional[socket.socket] = None
        self._udp = udp
        self.rec: Optional[Recorder] = None
        if record:
            self.rec = Recorder(rec_dir, wall_offset=self.wall_offset, **(rec_kwargs or {}))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._thr = {
            "impact_g": cfg.IMPACT_G,
            "freefall_g": cfg.FREEFALL_G,
            "gyro_gate_dps": cfg.US_GYRO_GATE_DPS,
            "warn_m": cfg.US_WARN_DISTANCE_M,
            "min_closing_mps": cfg.US_MIN_OBJ_MPS,
            "max_range_m": cfg.US_MAX_RANGE_M,
            "buzz_on_s": cfg.HAPTIC_WARN_ON_S,
        }

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        try:
            if self.rec is not None and self.rec.start():
                self.rec.put(("e", time.perf_counter(), "meta", "", f"session={self.rec.session} boot={self.boot}"))
            elif self.rec is not None:
                self.rec = None
            if self._udp:
                self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self._sock.setblocking(False)
            self._thread = threading.Thread(target=self._run, name="live-pub", daemon=True)
            self._thread.start()
            where = f"udp {self.host}:{self.port}" if self._udp else "no udp"
            rec = f"recording {self.rec.dir}" if self.rec else "no recording"
            print(f"[live] telemetry on: {where}, {rec}", flush=True)
        except Exception as e:  # never let telemetry stop hat.main from starting
            print(f"[live] telemetry failed to start: {e}", flush=True)

    def stop(self) -> None:
        try:
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=1.0)
            if self.rec is not None:
                self.rec.stop()
            if self._sock is not None:
                self._sock.close()
        except Exception:
            pass

    # ------------------------------------------------------------ hot-path hooks (no I/O, no raise)
    def imu(self, s: Any) -> None:
        try:
            self._imu = s
            if self.rec is not None:
                self.rec.put(("i", s.t, s.ax, s.ay, s.az, s.gx, s.gy, s.gz))
        except Exception:
            pass

    def us(self, t: float, side: str, raw: Optional[float], det: Any, motion: Any) -> None:
        try:
            gated = bool(det.last_gated)
            moving = bool(motion.moving(t))
            trk, clo = det.last_distance, det.last_closing
            self._us[side] = (t, raw, trk, clo, gated, moving)
            if self.rec is not None:
                self.rec.put(("u", t, side, raw, trk, clo, gated, moving))
        except Exception:
            pass

    def wear(self, snap: Any) -> None:
        try:
            t = time.perf_counter()
            worn, mm, near = bool(snap.helmet_worn), snap.distance_mm, bool(snap.near)
            self._tof = (t, worn, mm, near)
            if self.rec is not None:
                self.rec.put(("e", t, "wear", "", f"worn={int(worn)} mm={'' if mm is None else mm} near={int(near)}"))
            if worn != self._last_worn:
                if self._last_worn is not None or worn:
                    self.event("hat_on" if worn else "hat_off", None, f"tof {mm if mm is not None else '--'} mm")
                self._last_worn = worn
        except Exception:
            pass

    def event(self, kind: str, side: Optional[str] = None, text: str = "") -> None:
        try:
            t = time.perf_counter()
            self._ev_id += 1
            self._events.append({"id": self._ev_id, "t": round(self.wall_offset + t, 3), "kind": kind,
                                 "side": side, "text": text})
            if self.rec is not None:
                self.rec.put(("e", t, kind, side, text))
        except Exception:
            pass

    def i2c_error(self) -> None:
        self.i2c_errors += 1

    # ------------------------------------------------------------ publisher thread
    def _run(self) -> None:
        nxt = time.perf_counter()
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                pass
            nxt += self.period
            wait = nxt - time.perf_counter()
            if wait < 0:
                nxt = time.perf_counter()
                wait = 0.0
            self._stop.wait(wait)

    def _tick(self) -> None:
        now = time.perf_counter()
        self._note_buzz_pulses(now)
        if self._sock is None:
            return
        data = json.dumps(self.snapshot(now), separators=(",", ":")).encode()
        try:
            self._sock.sendto(data, (self.host, self.port))
        except OSError:  # EAGAIN / ECONNREFUSED / no route: nobody to tell
            self.send_errors += 1

    def _note_buzz_pulses(self, now: float) -> None:
        h = getattr(self.src, "haptics", None)
        lf = getattr(h, "last_fire", None)
        if not lf or self.rec is None:
            return
        for side in ("left", "right"):
            t = lf.get(side, 0.0)
            if t != self._last_fire[side]:
                self._last_fire[side] = t
                self.rec.put(("e", t, "buzz_pulse", side, ""))

    def snapshot(self, now: Optional[float] = None) -> Dict[str, Any]:
        """Compact dict for the viewer. Reads other threads' state without locks (single values, GIL)."""
        now = time.perf_counter() if now is None else now
        src = self.src
        self.seq += 1
        s = self._imu
        snap: Dict[str, Any] = {
            "v": 1, "boot": self.boot, "seq": self.seq, "t": round(time.time(), 3),
            "up": round(now - self.t_start, 1), "thr": self._thr,
        }
        if s is not None:
            snap.update(
                ax=_r(s.ax), ay=_r(s.ay), az=_r(s.az), mag=_r(s.mag),
                gx=_r(s.gx, 2), gy=_r(s.gy, 2), gz=_r(s.gz, 2),
                imu_age=round(now - s.t, 2),
            )
        else:
            snap.update(ax=None, ay=None, az=None, mag=None, gx=None, gy=None, gz=None, imu_age=None)
        for key, side in (("l", "left"), ("r", "right")):
            det = getattr(src, f"approach_{side}", None)
            u = self._us.get(side)
            snap[f"{key}_raw"] = _r(u[1]) if u else None
            snap[f"{key}_dist"] = _r(det.last_distance, 3) if det is not None else None
            snap[f"{key}_close"] = _r(det.last_closing, 2) if det is not None and det.last_distance is not None else None
            snap[f"{key}_age"] = round(now - u[0], 2) if u else None
            fire = getattr(getattr(src, "haptics", None), "last_fire", None) or {}
            t_fire = fire.get(side, 0.0)
            age = now - t_fire if t_fire else None
            snap[f"{key}_buzz_age"] = None if age is None else round(age, 2)
            snap[f"{key}_buzz"] = bool(age is not None and age < cfg.HAPTIC_WARN_ON_S + 0.05)
        us = list(self._us.values())
        snap["gated"] = any(u[4] for u in us if now - u[0] < 1.0)
        snap["moving"] = any(u[5] for u in us if now - u[0] < 1.0)
        ws = getattr(src, "wear_state", None)
        tof = self._tof
        snap["hat_on"] = bool(ws.current_worn) if ws is not None and ws.ready else None
        snap["tof_mm"] = tof[2] if tof and now - tof[0] < 1.0 else None
        snap["tof_near"] = tof[3] if tof and now - tof[0] < 1.0 else None
        det = getattr(src, "detector", None)
        snap["fall"] = det.status(now) if det is not None and hasattr(det, "status") else None
        imu = getattr(src, "imu", None)
        snap["config_losses"] = int(getattr(imu, "config_losses", 0) or 0)
        snap["i2c_errors"] = self.i2c_errors + int(getattr(imu, "i2c_errors", 0) or 0)
        snap["camera"] = bool(getattr(getattr(src, "camera", None), "_busy", False))
        snap["gyro_cal"] = bool(s is not None and s.gx is not None)
        evs = list(self._events)
        snap["event"] = evs[-1] if evs else None
        snap["events"] = evs
        if self.rec is not None:
            snap["rec"] = {"dropped": self.rec.dropped, "written": self.rec.written, "q": len(self.rec.q)}
        return snap


def _r(v: Optional[float], nd: int = 3) -> Optional[float]:
    return None if v is None else round(v, nd)


def create(src: Any) -> NullTelemetry:
    """Telemetry per hat/config.py (+ env overrides). Always returns an object with the hooks; never raises."""
    try:
        on = os.environ.get("ARGUS_LIVE_TELEMETRY", "1" if cfg.LIVE_TELEMETRY else "0") != "0"
        if not on:
            return NullTelemetry()
        rec = os.environ.get("ARGUS_LIVE_RECORD", "1" if cfg.LIVE_RECORD else "0") != "0"
        return Telemetry(
            src,
            record=rec,
            host=cfg.LIVE_UDP_HOST,
            port=int(os.environ.get("ARGUS_LIVE_PORT", cfg.LIVE_UDP_PORT)),
            hz=cfg.LIVE_HZ,
            rec_dir=os.environ.get("ARGUS_LIVE_DIR", cfg.LIVE_REC_DIR),
            rec_kwargs=dict(
                queue_size=cfg.LIVE_REC_QUEUE,
                segment_mb=cfg.LIVE_REC_SEGMENT_MB,
                max_mb=cfg.LIVE_REC_MAX_MB,
                keep_s=cfg.LIVE_REC_KEEP_S,
            ),
        )
    except Exception as e:
        print(f"[live] telemetry disabled: {e}", flush=True)
        return NullTelemetry()
