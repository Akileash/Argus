"""Mock camera with a real video source: video: {file, event_at_s, scenario_event_s}."""

import subprocess

import numpy as np
import pytest

import hardware.mock.camera as cam
from argus.contracts import ModuleContext
from hardware.mock.clock import VirtualClock

needs_ffmpeg = pytest.mark.skipif(cam.find_ffmpeg() is None, reason="ffmpeg not installed")

FALL = {"file": str(cam.DEVICE_DIR / "fixtures" / "clips" / "fall-from-ledge.mp4"),
        "event_at_s": 0.8, "scenario_event_s": 3.0}


def make(tmp_path, **opts):
    clock = VirtualClock()
    c = cam.create(ModuleContext(clock, {"cache_dir": str(tmp_path / "cache"), **opts}, "idle"))
    c.start()
    return clock, c


def _gray_frames(path, w=64):
    raw = subprocess.run([cam.find_ffmpeg(), "-v", "error", "-i", str(path), "-vf", f"scale={w}:{w},format=gray",
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, w * w).astype(float)


@needs_ffmpeg
def test_filter_mapping_hold_play_hold(tmp_path):
    _, c = make(tmp_path, size="640x480", fps=30, video=FALL)
    spec, dur = c._video, 1.5333
    # video time at the start of scenario second k: v0 = k - 3.0 + 0.8
    f1 = c.video_filter(1, spec, dur)  # v0 = -1.2: clone the first frame for 1.2 s
    assert "start_duration=1.200000" in f1 and "trim=start=0.000000" in f1
    f2 = c.video_filter(2, spec, dur)  # v0 = -0.2
    assert "start_duration=0.200000" in f2 and "trim=start=0.000000" in f2
    f3 = c.video_filter(3, spec, dur)  # v0 = 0.8: plays, no leading clone
    assert "start_duration=0.000000" in f3 and "trim=start=0.800000" in f3
    f4 = c.video_filter(4, spec, dur)  # v0 = 1.8 > duration: only the last frame
    assert "trim=start=1.800000" in f4 and "scale=640:480" in f4 and "pad=640:480" in f4


@needs_ffmpeg
def test_segments_align_event_and_concat_copy(tmp_path):
    clock, c = make(tmp_path, size="320x240", fps=15, video=FALL)
    clock.advance(8.0)
    info = c.save_clip(0, clock.now_ns(), str(tmp_path / "v.mp4"))
    assert info.duration_s == 8.0
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-count_frames", "-show_entries",
                          "stream=codec_name,width,height,pix_fmt,r_frame_rate,nb_read_frames",
                          "-of", "default=nw=1", info.path], capture_output=True, text=True, check=True).stdout
    for want in ("codec_name=h264", "width=320", "height=240", "pix_fmt=yuv420p", "r_frame_rate=15/1",
                 "nb_read_frames=120"):  # every segment is exactly 15 frames: 8 s * 15
        assert want in out, out
    f = _gray_frames(info.path)
    d = np.abs(np.diff(f, axis=0)).mean(axis=1)  # per-frame change; frame i is at i/15 s
    assert d[:31].max() < 1.0  # first frame held until the video starts at 3.0 - 0.8 = 2.2 s (frame 33)
    assert d[36:56].mean() > 1.5  # the worker moves while the video plays (2.2 s .. 3.73 s)
    assert d[60:].max() < 1.0  # last frame held after the video ends at 3.0 + 0.73 = 3.73 s (frame 56)
    assert np.abs(f[0] - f[30]).mean() < 1.0  # held frame == first frame


@needs_ffmpeg
def test_segments_cached_per_source_and_params(tmp_path):
    clock, c = make(tmp_path, size="320x240", fps=15, video=FALL)
    clock.advance(5.0)
    c.save_clip(0, clock.now_ns(), str(tmp_path / "a.mp4"))
    segs = sorted((tmp_path / "cache").rglob("*.ts"))
    assert len(segs) == 5 and segs[0].parent.name.startswith("video_")
    mtimes = [p.stat().st_mtime_ns for p in segs]
    c.save_clip(0, clock.now_ns(), str(tmp_path / "b.mp4"))
    assert [p.stat().st_mtime_ns for p in sorted((tmp_path / "cache").rglob("*.ts"))] == mtimes  # reused
    _, c2 = make(tmp_path, size="320x240", fps=15, video={**FALL, "scenario_event_s": 2.0})
    assert c2.cache != c.cache  # other alignment: own cache, never stale segments


@needs_ffmpeg
def test_no_video_source_keeps_test_pattern(tmp_path):
    _, c = make(tmp_path)
    assert c._video is None and c.cache.name == "seg_320x240_15"


def test_hat_drop_scenario_points_at_the_shipped_video():
    from hardware.mock.scenario import load_scenario
    v = load_scenario("hat_drop_1m").spec.video
    assert v is not None and v.scenario_event_s == 3.0  # == free_fall t
    assert (cam.DEVICE_DIR / v.file).stat().st_size < 2_000_000
