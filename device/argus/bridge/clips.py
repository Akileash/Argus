"""Find the clip for an event: wait for ``clips/<event_id>.mp4`` to stop growing; remux a copy of a lone ``.h264``.

Their folder is read only. The remuxed copy goes to our own staging dir.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("argus.bridge.clips")


@dataclass(frozen=True)
class ClipPolicy:
    wait_s: float = 45.0
    poll_s: float = 0.5
    stable_s: float = 2.0  # size unchanged this long = finished
    h264_grace_s: float = 12.0  # a stable .h264 with no .mp4 after this long means their remux did not happen


def _size(p: Path) -> int | None:
    try:
        return p.stat().st_size
    except OSError:
        return None


def remux_h264(src: Path, dst: Path) -> bool:
    """``ffmpeg -c copy`` of a copy, not touching ``src``. False when ffmpeg is missing or fails."""
    ff = shutil.which("ffmpeg")
    if not ff:
        log.error("ffmpeg not found: cannot remux %s", src)
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.stem + ".part.mp4")
    r = subprocess.run([ff, "-y", "-loglevel", "error", "-framerate", "30", "-i", str(src), "-c", "copy",
                        "-movflags", "+faststart", str(tmp)], check=False, capture_output=True, text=True, timeout=120)
    if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        log.error("ffmpeg remux of %s failed: %s", src, r.stderr.strip()[-300:])
        tmp.unlink(missing_ok=True)
        return False
    tmp.replace(dst)
    return True


def probe_duration_s(path: Path) -> float | None:
    fp = shutil.which("ffprobe")
    if not fp:
        return None
    try:
        r = subprocess.run([fp, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1",
                            str(path)], check=False, capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def wait_for_clip(event_id: str, clips_dir: Path, staging: Path, policy: ClipPolicy, *,
                  sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                  stop: Callable[[], bool] = lambda: False) -> Path | None:
    """Path of a finished mp4 (theirs, or our remuxed copy), or None when no clip showed up in time."""
    mp4, h264 = clips_dir / f"{event_id}.mp4", clips_dir / f"{event_id}.h264"
    deadline = clock() + policy.wait_s
    last: dict[Path, tuple[int, float]] = {}

    def stable(p: Path) -> bool:
        sz = _size(p)
        if not sz:
            last.pop(p, None)
            return False
        prev = last.get(p)
        now = clock()
        if prev is None or prev[0] != sz:
            last[p] = (sz, now)
            return False
        return now - prev[1] >= policy.stable_s

    h264_since: float | None = None
    while True:
        if stable(mp4):
            return mp4
        if stable(h264):
            h264_since = h264_since if h264_since is not None else clock()
            if clock() - h264_since >= policy.h264_grace_s or clock() + policy.poll_s >= deadline:
                break
        if clock() >= deadline or stop():
            break
        sleep(policy.poll_s)
    if _size(mp4) and stable(mp4):
        return mp4
    if _size(h264):
        out = staging / f"{event_id}.mp4"
        if remux_h264(h264, out):
            log.info("clip for %s: remuxed from .h264 into %s", event_id, out)
            return out
    return None
