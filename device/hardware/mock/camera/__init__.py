"""Mock camera: a ring of ffmpeg-generated H.264 segments (spec §5.2, §4.4).

Video is ``testsrc2`` (it burns in a running timestamp), cut into 1 s MPEG-TS segments whose content
depends only on the scenario second, so they are generated lazily on the first ``save_clip`` and
cached under ``device/var/mock_camera/``. ``save_clip`` concatenates the covered segments into a real
MP4 with ``ffmpeg -c copy`` (no re-encode), exactly like the production clip cutter. Clip bounds
are therefore rounded outwards to whole seconds; ``ClipInfo`` reports the actual bounds.

A scenario (or the camera option ``video``) may name a real video file: ``{file, event_at_s,
scenario_event_s}``. Scenario second ``k`` is then cut from that file instead of ``testsrc2``, with the
video's ``event_at_s`` placed on scenario second ``scenario_event_s`` (first frame held before the video,
last frame held after it, real speed during it). Every segment is normalised to the camera size/fps.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from argus.contracts import Camera, ClipInfo, Health, ModuleContext
from hardware.mock.scenario import ScenarioRun, VideoSpec, get_run

log = logging.getLogger(__name__)
DEVICE_DIR = Path(__file__).resolve().parents[3]
DEFAULT_CACHE = Path(__file__).resolve().parents[3] / "var" / "mock_camera"
FFMPEG_FALLBACK = "/opt/homebrew/bin/ffmpeg"
MAX_CLIP_S = 90


class CameraError(RuntimeError):
    """save_clip could not produce a clip (ffmpeg missing, window outside the ring, ffmpeg failure)."""


def find_ffmpeg() -> str | None:
    return os.environ.get("ARGUS_FFMPEG") or shutil.which("ffmpeg") or (
        FFMPEG_FALLBACK if Path(FFMPEG_FALLBACK).exists() else None
    )


class MockCamera:
    def __init__(self, ctx: ModuleContext) -> None:
        self.ctx = ctx
        self.clock = ctx.clock
        o = ctx.options
        self.ring_s = float(o.get("ring_s", 35))
        self.size = str(o.get("size", "320x240"))
        self.fps = int(o.get("fps", 15))
        self.cache = Path(o.get("cache_dir", DEFAULT_CACHE)) / f"seg_{self.size}_{self.fps}"
        self._ffmpeg = find_ffmpeg()
        self._video_opt = o.get("video")
        self._video: VideoSpec | None = None
        self._video_dur = 0.0
        self._run: ScenarioRun | None = None
        self._running = False
        self._dead = False
        self._errors = 0

    def start(self) -> None:
        self._run = get_run(self.ctx.scenario or self.ctx.options.get("scenario"), self.clock)
        self._video = self._resolve_video()
        self._running = True

    def stop(self) -> None:
        self._running = False

    def kill(self) -> None:
        self._dead = True

    def revive(self) -> None:
        self._dead = False

    def health(self) -> Health:
        if self._dead:
            return Health(False, None, self._errors, "killed (fault injection)")
        if self._ffmpeg is None:
            return Health(False, None, self._errors, "ffmpeg not found on PATH (set ARGUS_FFMPEG)")
        return Health(self._running, 0.0 if self._running else None, self._errors,
                      "" if self._running else "not started")

    # -- clips
    def save_clip(self, t_start_ns: int, t_end_ns: int, path: str) -> ClipInfo:
        try:
            return self._save(t_start_ns, t_end_ns, Path(path))
        except CameraError:
            self._errors += 1
            raise
        except Exception as e:
            self._errors += 1
            raise CameraError(f"mock camera save_clip failed: {e}") from e

    def _save(self, t_start_ns: int, t_end_ns: int, out: Path) -> ClipInfo:
        if self._dead or not self._running or self._run is None:
            raise CameraError("mock camera is not running")
        if self._ffmpeg is None:
            raise CameraError("ffmpeg not found: install it (brew install ffmpeg) or set ARGUS_FFMPEG")
        run = self._run
        now_s = run.t_s(self.clock.now_ns())
        lo = max(math.floor(run.t_s(t_start_ns)), 0, math.floor(now_s - self.ring_s))
        hi = min(math.ceil(run.t_s(t_end_ns)), math.floor(now_s))  # only whole elapsed seconds exist
        hi = min(hi, lo + MAX_CLIP_S)
        if hi <= lo:
            raise CameraError("requested window is outside the camera ring buffer")
        segs = self._ensure_segments(lo, hi - 1)
        out.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as td:
            lst = Path(td) / "list.txt"
            lst.write_text("".join(f"file '{p}'\n" for p in segs))
            self._ffmpeg_run(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                              "-movflags", "+faststart", str(out)])
        return ClipInfo(str(out), run.t0_ns + lo * 10**9, run.t0_ns + hi * 10**9, float(hi - lo),
                        out.stat().st_size)

    # -- real video source
    def _resolve_video(self) -> VideoSpec | None:
        assert self._run is not None
        v = self._video_opt
        spec = VideoSpec.model_validate(v) if v else self._run.scenario.spec.video
        if spec is None:
            return None
        path = Path(spec.file)
        if not path.is_absolute():
            path = DEVICE_DIR / path
        spec = spec.model_copy(update={"file": str(path)})
        w, h = (int(x) for x in self.size.lower().split("x"))
        if w % 2 or h % 2:
            raise CameraError(f"camera size {self.size} must have even dimensions (H.264 yuv420p)")
        self._cache_base = Path(self.ctx.options.get("cache_dir", DEFAULT_CACHE)) / f"seg_{self.size}_{self.fps}"
        digest = hashlib.sha256(path.read_bytes() if path.exists() else b"missing")
        digest.update(f"{spec.event_at_s}|{spec.scenario_event_s}|{self.size}|{self.fps}".encode())
        self.cache = self._cache_base / f"video_{digest.hexdigest()[:16]}"
        return spec

    def _video_duration(self, spec: VideoSpec) -> float:
        if not self._video_dur:
            if not Path(spec.file).exists():
                raise CameraError(f"video source not found: {spec.file}")
            ffprobe = str(Path(self._ffmpeg or "").with_name("ffprobe")) if self._ffmpeg else "ffprobe"
            r = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of",
                                "default=nw=1:nk=1", spec.file], capture_output=True, text=True, check=False)
            try:
                self._video_dur = float(r.stdout.strip())
            except ValueError as e:
                raise CameraError(f"cannot read video duration of {spec.file}: {r.stderr.strip()[-200:]}") from e
        return self._video_dur

    def video_filter(self, k: int, spec: VideoSpec, dur: float) -> str:
        """ffmpeg -vf chain that yields the frames of scenario second ``k`` from the video source.

        ``v0`` is the video time at the start of the segment; negative = before the video starts (first
        frame cloned), beyond ``dur`` = after it ended (last frame cloned).
        """
        w, h = self.size.lower().split("x")
        fps = self.fps
        v0 = k - spec.scenario_event_s + spec.event_at_s
        pre = math.ceil(-v0 * fps - 1e-6) / fps if v0 < 0 else 0.0  # whole frames of clone before the video
        post = max(0.0, v0 + 1 - dur) + 1.0
        return (f"fps={fps},scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,format=yuv420p,"
                f"tpad=start_mode=clone:start_duration={pre:.6f}:stop_mode=clone:stop_duration={post:.6f},"
                f"trim=start={max(v0 + pre, 0.0):.6f},setpts=PTS-STARTPTS")

    def _seg_path(self, k: int) -> Path:
        return self.cache / f"seg_{k:06d}.ts"

    def _ensure_segments(self, first: int, last: int) -> list[Path]:
        self.cache.mkdir(parents=True, exist_ok=True)
        for k in range(first, last + 1):
            if not self._seg_path(k).exists():
                self._generate(k)
        return [self._seg_path(k) for k in range(first, last + 1)]

    def _generate(self, k: int) -> None:
        """Encode scenario second ``k`` as its own file.

        One ffmpeg per second on purpose: the segment muxer puts 2 s in the first file when the
        input starts at t=0, which left one file too few and shifted every later second.
        """
        with tempfile.TemporaryDirectory(dir=self.cache) as td:
            tmp = Path(td) / "seg.ts"
            if self._video is not None:
                src = ["-i", self._video.file, "-vf", self.video_filter(k, self._video,
                                                                       self._video_duration(self._video)),
                       "-frames:v", str(self.fps), "-r", str(self.fps), "-an"]
            else:
                src = ["-ss", str(k), "-f", "lavfi", "-i", f"testsrc2=size={self.size}:rate={self.fps}",
                       "-t", "1"]
            self._ffmpeg_run([
                *src, "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-g", str(self.fps), "-keyint_min", str(self.fps), "-sc_threshold", "0",
                "-f", "mpegts", str(tmp),
            ])
            if not tmp.exists() or tmp.stat().st_size == 0:
                raise CameraError(f"ffmpeg produced no segment for second {k}")
            tmp.replace(self._seg_path(k))

    def _ffmpeg_run(self, args: list[str]) -> None:
        assert self._ffmpeg
        r = subprocess.run([self._ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args],
                           capture_output=True, text=True, timeout=60, check=False)
        if r.returncode != 0:
            raise CameraError(f"ffmpeg failed: {r.stderr.strip()[-400:]}")


def create(ctx: ModuleContext) -> Camera:
    return MockCamera(ctx)
