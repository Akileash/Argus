import shutil
import subprocess

import pytest

import hardware.mock.camera as cam
from argus.contracts import ModuleContext
from hardware.mock.clock import VirtualClock

needs_ffmpeg = pytest.mark.skipif(cam.find_ffmpeg() is None, reason="ffmpeg not installed")


def make(tmp_path, **opts):
    clock = VirtualClock()
    c = cam.create(ModuleContext(clock, {"cache_dir": str(tmp_path / "cache"), **opts}, "idle"))
    c.start()
    return clock, c


def test_missing_ffmpeg_is_a_clear_error_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(cam, "find_ffmpeg", lambda: None)
    clock, c = make(tmp_path)
    assert not c.health().ok and "ffmpeg not found" in c.health().note
    clock.advance(5)
    with pytest.raises(cam.CameraError, match="ffmpeg not found"):
        c.save_clip(0, clock.now_ns(), str(tmp_path / "x.mp4"))
    assert c.health().error_count == 1


@needs_ffmpeg
def test_clip_is_real_h264_mp4_with_segments_cached(tmp_path):
    clock, c = make(tmp_path)
    clock.advance(8.0)
    t1 = clock.now_ns()
    info = c.save_clip(t1 - 3_000_000_000 - 400_000_000, t1 - 400_000_000, str(tmp_path / "a" / "clip.mp4"))
    assert info.duration_s == 4.0  # rounded outwards to whole seconds
    assert info.t_start_ns <= t1 - 3_400_000_000 and info.t_end_ns >= t1 - 400_000_000
    if shutil.which("ffprobe"):
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_name",
                              "-of", "default=nw=1", info.path], capture_output=True, text=True, check=True).stdout
        assert "codec_name=h264" in out and 3.5 < float(out.split("duration=")[1]) < 4.5
    n_cached = len(list((tmp_path / "cache").rglob("*.ts")))
    assert n_cached >= 4
    c.save_clip(t1 - 3_000_000_000, t1 - 1_000_000_000, str(tmp_path / "b.mp4"))
    assert len(list((tmp_path / "cache").rglob("*.ts"))) == n_cached  # reused, not regenerated


@needs_ffmpeg
def test_window_outside_ring_is_rejected_and_old_footage_clamped(tmp_path):
    clock, c = make(tmp_path, ring_s=5)
    clock.advance(20.0)
    now = clock.now_ns()
    with pytest.raises(cam.CameraError, match="outside"):
        c.save_clip(now - 19_000_000_000, now - 12_000_000_000, str(tmp_path / "old.mp4"))
    info = c.save_clip(now - 19_000_000_000, now, str(tmp_path / "ok.mp4"))
    assert info.duration_s <= 6.0  # only the 5 s ring (+ rounding) is available
