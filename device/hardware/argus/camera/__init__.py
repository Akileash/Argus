"""Front Pi Camera (IMX219) -> hardware H.264 -> CircularOutput ring -> clip on demand.

Spec §3.1: binned 1640x1232 sensor mode, 1280x720 @ 30 fps output, hardware encoder, ring >= 30 s
(pre 20 s + post 10 s). save_clip() dumps the ring to .h264, then `ffmpeg -c copy` -> MP4 (no re-encode).

Clock mapping. libcamera's SensorTimestamp (what picamera2 passes to the output as `timestamp`, in
microseconds) is a boottime-style clock (CLOCK_BOOTTIME on Linux; it includes suspend time). The
shared clock is CLOCK_MONOTONIC (time.monotonic_ns), which excludes suspend. They differ by a
constant offset: offset = CLOCK_BOOTTIME - CLOCK_MONOTONIC, sampled back-to-back each frame
(`boottime_offset_ns`). The `timestamp_clock` option ("boottime" default | "monotonic") lets the bench
team flip this if `argus-cli test camera` shows the frame age is not ~one frame period.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Protocol

from argus.contracts import ClipInfo, Health, ModuleContext

from .._common import HealthTracker, log


def boottime_offset_ns() -> int:
    """CLOCK_BOOTTIME - CLOCK_MONOTONIC in ns (0 on platforms without CLOCK_BOOTTIME)."""
    cb = getattr(time, "CLOCK_BOOTTIME", None)
    if cb is None:
        return 0
    return time.clock_gettime_ns(cb) - time.monotonic_ns()


class CameraBackend(Protocol):
    def start(self, on_frame: Callable[[int, bool], None]) -> None:
        """Begin capture. on_frame(timestamp_ns_native, is_keyframe) is called per encoded frame."""
        ...

    def dump(self, path: str) -> None:
        """Write the current ring contents (from its first keyframe) to a raw .h264 file."""
        ...

    def stop(self) -> None: ...


class Picamera2Backend:
    def __init__(self, width: int, height: int, fps: int, bitrate: int, ring_frames: int, sensor_size) -> None:
        self.w, self.h, self.fps, self.bitrate = width, height, fps, bitrate
        self.ring_frames = ring_frames
        self.sensor_size = tuple(sensor_size)
        self._cam = None
        self._enc = None
        self._out = None

    def start(self, on_frame: Callable[[int, bool], None]) -> None:
        from picamera2 import Picamera2  # lazy
        from picamera2.encoders import H264Encoder  # lazy
        from picamera2.outputs import CircularOutput  # lazy

        class _TimedCircularOutput(CircularOutput):
            def outputframe(self, frame, keyframe=True, timestamp=None, *a, **k):
                if timestamp is not None:
                    on_frame(int(timestamp) * 1000, bool(keyframe))  # picamera2 gives microseconds
                return super().outputframe(frame, keyframe, timestamp, *a, **k)

        cam = Picamera2()
        cfg = cam.create_video_configuration(
            main={"size": (self.w, self.h), "format": "YUV420"},
            sensor={"output_size": self.sensor_size, "bit_depth": 10},  # binned full-FOV mode
            controls={"FrameDurationLimits": (int(1e6 / self.fps), int(1e6 / self.fps))},
        )
        cam.configure(cfg)
        # iperiod=fps -> a keyframe every second so clips can start close to the requested time;
        # repeat=True -> SPS/PPS before every IDR so cut clips are decodable.
        enc = H264Encoder(bitrate=self.bitrate, repeat=True, iperiod=self.fps)
        out = _TimedCircularOutput(buffersize=self.ring_frames)
        cam.start_recording(enc, out)
        self._cam, self._enc, self._out = cam, enc, out

    def dump(self, path: str) -> None:
        out = self._out
        out.fileoutput = path
        out.start()  # flushes the ring from its first keyframe, then keeps writing live frames
        out.stop()

    def stop(self) -> None:
        if self._cam is not None:
            try:
                self._cam.stop_recording()
            finally:
                self._cam.close()
                self._cam = None


def run_ffmpeg(args: list[str]) -> None:
    subprocess.run(args, check=True, capture_output=True, timeout=60)


class PiCamera:
    def __init__(
        self,
        ctx: ModuleContext,
        backend: CameraBackend | None = None,
        muxer: Callable[[list[str]], None] = run_ffmpeg,
        offset_fn: Callable[[], int] = boottime_offset_ns,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        o = ctx.options
        self._clock = ctx.clock
        self._fps = int(o.get("fps", 30))
        self._ring_s = float(o.get("ring_seconds", 40))  # >= 30 s required; 40 gives margin
        self._ring_frames = int(self._ring_s * self._fps)
        self._backend = backend or Picamera2Backend(
            int(o.get("width", 1280)),
            int(o.get("height", 720)),
            self._fps,
            int(o.get("bitrate", 3_000_000)),
            self._ring_frames,
            o.get("sensor_size", (1640, 1232)),
        )
        self._muxer = muxer
        self._offset_fn = offset_fn
        self._sleep = sleep
        self._ts_clock = str(o.get("timestamp_clock", "boottime"))
        self._ffmpeg = str(o.get("ffmpeg", "ffmpeg"))
        self._tmp_dir = o.get("tmp_dir")
        self._post_wait_s = float(o.get("max_post_wait_s", 15))
        # index mirrors the CircularOutput ring: (t_mono_ns, keyframe) for the last ring_frames frames
        self._idx: deque[tuple[int, bool]] = deque(maxlen=self._ring_frames)
        self._lock = threading.Lock()
        self._health = HealthTracker(ctx.clock, stale_ms=float(o.get("stale_ms", 2000)))
        self._running = False

    def _on_frame(self, ts_native_ns: int, keyframe: bool) -> None:
        t = ts_native_ns - self._offset_fn() if self._ts_clock == "boottime" else ts_native_ns
        with self._lock:
            self._idx.append((t, keyframe))
        self._health.sample()

    def start(self) -> None:
        if self._running:
            return
        self._health.mark_started()
        try:
            self._backend.start(self._on_frame)
            self._running = True
        except Exception as e:
            self._health.error(f"camera init failed: {e}")

    def stop(self) -> None:
        if self._running:
            try:
                self._backend.stop()
            except Exception as e:
                self._health.error(f"camera stop failed: {e}")
            self._running = False

    def _latest_ts(self) -> int | None:
        with self._lock:
            return self._idx[-1][0] if self._idx else None

    def save_clip(self, t_start_ns: int, t_end_ns: int, path: str) -> ClipInfo:
        try:
            return self._save_clip(t_start_ns, t_end_ns, path)
        except Exception as e:
            self._health.error(f"save_clip failed: {e}")
            raise

    def _save_clip(self, t_start_ns: int, t_end_ns: int, path: str) -> ClipInfo:
        if not self._running:
            raise RuntimeError("camera not running")
        # wait (bounded) for the post-roll to be encoded
        deadline = time.monotonic() + self._post_wait_s
        while (self._latest_ts() or 0) < t_end_ns and time.monotonic() < deadline:
            self._sleep(0.05)
        with self._lock:
            frames = list(self._idx)
        if not frames:
            raise RuntimeError("no frames in ring buffer")
        first_kf = next((t for t, kf in frames if kf), frames[0][0])  # ring dump starts at a keyframe
        last = frames[-1][0]
        c_start = max(t_start_ns, first_kf)
        c_end = min(t_end_ns, last)
        if c_end <= c_start:
            raise RuntimeError("requested window is outside the ring buffer")
        raw = f"{path}.raw.h264"
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._backend.dump(raw)
        ss = (c_start - first_kf) / 1e9
        dur = (c_end - c_start) / 1e9
        # -c copy: stream copy only. Input -ss on a raw ES cuts on the previous keyframe (<= 1 s early).
        self._muxer(
            [self._ffmpeg, "-y", "-loglevel", "error", "-framerate", str(self._fps), "-f", "h264",
             "-ss", f"{ss:.3f}", "-i", raw, "-t", f"{dur:.3f}", "-c", "copy", "-movflags", "+faststart", path]
        )
        try:
            os.remove(raw)
        except OSError:
            log.warning("could not remove %s", raw)
        return ClipInfo(
            path=path,
            t_start_ns=int(c_start),
            t_end_ns=int(c_end),
            duration_s=dur,
            bytes=os.path.getsize(path),
        )

    def health(self) -> Health:
        return self._health.snapshot(running=self._running)


def create(ctx: ModuleContext) -> PiCamera:
    return PiCamera(ctx)
