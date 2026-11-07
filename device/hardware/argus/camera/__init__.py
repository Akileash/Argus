"""Front Pi Camera (IMX219) -> hardware H.264 -> our own frame ring -> clip on demand.

Spec §3.1: binned 1640x1232 sensor mode, 1280x720 @ 30 fps output, hardware encoder, ring >= 30 s
(pre 20 s + post 10 s).

Design (replaces picamera2's CircularOutput, which drains on dump and keeps no usable timestamps):

* A small picamera2 ``Output`` subclass hands every encoded frame to :class:`FrameRing`, which keeps a
  time-bounded deque of ``(t_ns, is_keyframe, bytes)``. ``t_ns`` is ``clock.now_ns()`` taken when the
  frame arrives from the encoder (shared CLOCK_MONOTONIC, so it lines up with every other stream). It
  lags the exposure by the encoder latency (tens of ms), which is far below the 1 s keyframe spacing.
  The ``timestamp`` argument picamera2 passes is deliberately NOT used: it is rebased to 0 at encoder
  start, so it is not comparable with the shared clock.
* ``save_clip(t_start, t_end, path)`` waits (bounded, without holding the ring lock) until a frame at or
  after ``t_end`` has arrived, takes a *copy* of the ring (nothing is drained, so overlapping or
  back-to-back events each get a full clip), starts at the last keyframe at or before ``t_start``,
  writes the raw Annex-B H.264 to a temp file and muxes it with
  ``ffmpeg -framerate <fps> -f h264 -i raw -c copy -movflags +faststart out.mp4`` (stream copy, no seek).
* ``ClipInfo.t_start_ns`` / ``t_end_ns`` are the first and last frame actually written, so the video
  starts up to one keyframe interval before the requested start.
"""

from __future__ import annotations

import os
import subprocess
import threading
from collections import deque
from collections.abc import Callable
from typing import Protocol

from argus.contracts import ClipInfo, Clock, Health, ModuleContext

from .._common import HealthTracker

Frame = tuple[int, bool, bytes]


class FrameRing:
    """Time-bounded ring of encoded frames. Thread-safe; ``snapshot`` copies, never drains."""

    def __init__(self, clock: Clock, ring_seconds: float) -> None:
        self._clock = clock
        self._ring_ns = int(ring_seconds * 1e9)
        self._frames: deque[Frame] = deque()
        self._cv = threading.Condition()

    def add(self, frame: bytes | memoryview, keyframe: bool) -> int:
        t = self._clock.now_ns()
        data = bytes(frame)  # the encoder may reuse its buffer
        with self._cv:
            self._frames.append((t, bool(keyframe), data))
            while len(self._frames) > 1 and t - self._frames[0][0] > self._ring_ns:
                self._frames.popleft()
            self._cv.notify_all()
        return t

    def latest_ns(self) -> int | None:
        with self._cv:
            return self._frames[-1][0] if self._frames else None

    def __len__(self) -> int:
        with self._cv:
            return len(self._frames)

    def wait_for(self, t_ns: int, timeout_s: float) -> bool:
        """Block (bounded) until a frame at or after ``t_ns`` has arrived. True if it did."""
        with self._cv:
            return self._cv.wait_for(lambda: bool(self._frames) and self._frames[-1][0] >= t_ns, max(0.0, timeout_s))

    def snapshot(self, t_start_ns: int, t_end_ns: int) -> list[Frame]:
        """Frames from the last keyframe at or before ``t_start`` (else the first keyframe in the ring)
        through the last frame with ``t <= t_end``. Raises RuntimeError if nothing usable."""
        with self._cv:
            frames = list(self._frames)
        if not frames:
            raise RuntimeError("no frames in ring buffer")
        keys = [i for i, f in enumerate(frames) if f[1]]
        if not keys:
            raise RuntimeError("no keyframe in ring buffer yet")
        start = next((i for i in reversed(keys) if frames[i][0] <= t_start_ns), keys[0])
        end = max((i for i, f in enumerate(frames) if f[0] <= t_end_ns), default=-1)
        if end < start:
            raise RuntimeError("requested window is outside the ring buffer")
        return frames[start : end + 1]


class CameraBackend(Protocol):
    def start(self, on_frame: Callable[[bytes, bool], None]) -> None:
        """Begin capture. ``on_frame(frame_bytes, is_keyframe)`` is called per encoded frame."""
        ...

    def stop(self) -> None: ...


class Picamera2Backend:
    def __init__(self, width: int, height: int, fps: int, bitrate: int, sensor_size) -> None:
        self.w, self.h, self.fps, self.bitrate = width, height, fps, bitrate
        self.sensor_size = tuple(sensor_size)
        self._cam = None

    def start(self, on_frame: Callable[[bytes, bool], None]) -> None:
        from picamera2 import Picamera2  # lazy
        from picamera2.encoders import H264Encoder  # lazy
        from picamera2.outputs import Output  # lazy

        class _RingOutput(Output):
            # picamera2's call signature varies by version (frame, keyframe, timestamp, packet, audio).
            def outputframe(self, frame, keyframe=True, *args, **kwargs):
                on_frame(frame, bool(keyframe))

        cam = Picamera2()
        cfg = cam.create_video_configuration(
            main={"size": (self.w, self.h), "format": "YUV420"},
            sensor={"output_size": self.sensor_size, "bit_depth": 10},  # binned full-FOV mode
            controls={"FrameDurationLimits": (int(1e6 / self.fps), int(1e6 / self.fps))},
        )
        cam.configure(cfg)
        # iperiod=fps -> a keyframe every second so clips can start close to the requested time;
        # repeat=True -> SPS/PPS inline before every IDR so a cut that starts at a keyframe is decodable.
        enc = H264Encoder(bitrate=self.bitrate, repeat=True, iperiod=self.fps)
        cam.start_recording(enc, _RingOutput())
        self._cam = cam

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
    ) -> None:
        o = ctx.options
        self._clock = ctx.clock
        self._fps = int(o.get("fps", 30))
        # `ring_s` is accepted too so the same key works for the mock and the real camera.
        self._ring_s = float(o.get("ring_seconds", o.get("ring_s", 40)))  # >= 30 s required; 40 gives margin
        self._ring = FrameRing(ctx.clock, self._ring_s)
        self._backend = backend or Picamera2Backend(
            int(o.get("width", 1280)),
            int(o.get("height", 720)),
            self._fps,
            int(o.get("bitrate", 3_000_000)),
            o.get("sensor_size", (1640, 1232)),
        )
        self._muxer = muxer
        self._ffmpeg = str(o.get("ffmpeg", "ffmpeg"))
        self._post_wait_s = float(o.get("max_post_wait_s", 15))
        self._health = HealthTracker(ctx.clock, stale_ms=float(o.get("stale_ms", 2000)))
        self._running = False

    def _on_frame(self, frame: bytes, keyframe: bool) -> None:
        self._ring.add(frame, keyframe)
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

    def save_clip(self, t_start_ns: int, t_end_ns: int, path: str) -> ClipInfo:
        try:
            return self._save_clip(t_start_ns, t_end_ns, path)
        except Exception as e:
            self._health.error(f"save_clip failed: {e}")
            raise

    def _save_clip(self, t_start_ns: int, t_end_ns: int, path: str) -> ClipInfo:
        if not self._running:
            raise RuntimeError("camera not running")
        # Wait (bounded) for the post-roll to be encoded. Only this caller blocks; the ring keeps filling.
        wait_s = min(self._post_wait_s, max(0.0, (t_end_ns - self._clock.now_ns()) / 1e9) + 1.0)
        self._ring.wait_for(t_end_ns, wait_s)
        frames = self._ring.snapshot(t_start_ns, t_end_ns)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        raw = f"{path}.raw.h264"
        try:
            with open(raw, "wb") as f:
                f.writelines(data for _, _, data in frames)
            # Stream copy of the whole raw file, no seek: `-ss` on a raw ES gave an empty MP4 on ffmpeg 9.
            self._muxer(
                [self._ffmpeg, "-y", "-loglevel", "error", "-framerate", str(self._fps), "-f", "h264",
                 "-i", raw, "-c", "copy", "-movflags", "+faststart", path]
            )
        finally:
            try:
                os.remove(raw)
            except OSError:
                pass
        n = len(frames)
        return ClipInfo(
            path=path,
            t_start_ns=int(frames[0][0]),
            t_end_ns=int(frames[-1][0]),
            duration_s=n / self._fps,
            bytes=os.path.getsize(path),
        )

    def health(self) -> Health:
        return self._health.snapshot(running=self._running)


def create(ctx: ModuleContext) -> PiCamera:
    return PiCamera(ctx)
