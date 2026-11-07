"""Mock camera: a ring of ffmpeg-generated H.264 segments (spec §5.2, §4.4).

Video is ``testsrc2`` (it burns in a running timestamp), cut into 1 s MPEG-TS segments whose content
depends only on the scenario second, so they are generated lazily on the first ``save_clip`` and
cached under ``device/var/mock_camera/``. ``save_clip`` concatenates the covered segments into a real
MP4 with ``ffmpeg -c copy`` (no re-encode), exactly like the production clip cutter. Clip bounds
are therefore rounded outwards to whole seconds; ``ClipInfo`` reports the actual bounds.
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from argus.contracts import Camera, ClipInfo, Health, ModuleContext
from hardware.mock.scenario import ScenarioRun, get_run

log = logging.getLogger(__name__)
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
        self._run: ScenarioRun | None = None
        self._running = False
        self._dead = False
        self._errors = 0

    def start(self) -> None:
        self._run = get_run(self.ctx.scenario or self.ctx.options.get("scenario"), self.clock)
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
            self._ffmpeg_run([
                "-ss", str(k), "-f", "lavfi", "-i", f"testsrc2=size={self.size}:rate={self.fps}",
                "-t", "1", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
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
