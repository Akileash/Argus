"""Regression: a clip window starting at scenario t=0 used to fail (segment muxer put 2 s in file 0)."""

import shutil
import subprocess

import pytest

from argus.contracts import ModuleContext
from hardware.mock.camera import create, find_ffmpeg
from hardware.mock.clock import VirtualClock

pytestmark = pytest.mark.skipif(find_ffmpeg() is None, reason="ffmpeg not available")


def test_clip_from_t0_has_the_full_duration(tmp_path):
    clock = VirtualClock()
    cam = create(ModuleContext(clock=clock, options={"cache_dir": str(tmp_path / "c")}, scenario="idle"))
    cam.start()
    t0 = clock.now_ns()
    clock.advance(6.0)
    out = tmp_path / "clip.mp4"
    info = cam.save_clip(t0, t0 + 5 * 10**9, str(out))
    assert info.duration_s == 5
    ffprobe = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
    dur = float(subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True).stdout)
    assert dur == pytest.approx(5.0, abs=0.3)
