"""Clip discovery: stable mp4, late mp4, .h264-only (remuxed copy), nothing."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from argus.bridge import clips
from argus.bridge.clips import wait_for_clip

from .conftest import FAST


def test_finished_mp4(tmp_path):
    (tmp_path / "e.mp4").write_bytes(b"x" * 100)
    assert wait_for_clip("e", tmp_path, tmp_path / "st", FAST) == tmp_path / "e.mp4"


def test_late_growing_mp4_is_only_returned_when_size_is_stable(tmp_path):
    from .conftest import Hat, make_cfg
    h = Hat(make_cfg(tmp_path))
    t = h.mp4_late("e", 0.3)
    try:
        got = wait_for_clip("e", h.clips, tmp_path / "st", FAST)
    finally:
        t.join()
    assert got == h.clips / "e.mp4" and got.stat().st_size == 5000  # not the half-written file


def test_no_clip_times_out(tmp_path):
    p = clips.ClipPolicy(wait_s=0.3, poll_s=0.02, stable_s=0.1)
    assert wait_for_clip("e", tmp_path, tmp_path / "st", p) is None


def test_h264_only_is_remuxed_into_our_staging_never_theirs(tmp_path, monkeypatch):
    src = tmp_path / "clips"
    src.mkdir()
    (src / "e.h264").write_bytes(b"h" * 5000)
    calls = []

    def fake_remux(a, b):
        calls.append((a, b))
        b.parent.mkdir(parents=True, exist_ok=True)
        b.write_bytes(b"m" * 4000)
        return True

    monkeypatch.setattr(clips, "remux_h264", fake_remux)
    got = wait_for_clip("e", src, tmp_path / "staging", FAST)
    assert got == tmp_path / "staging" / "e.mp4" and calls[0][0] == src / "e.h264"
    assert sorted(p.name for p in src.iterdir()) == ["e.h264"]  # their folder untouched


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_real_ffmpeg_remux_copy(tmp_path):
    src = tmp_path / "clips"
    src.mkdir()
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30", "-t", "1",
                    "-c:v", "libx264", "-f", "h264", str(src / "e.h264")], check=True)
    got = wait_for_clip("e", src, tmp_path / "staging", FAST)
    assert got is not None and got.suffix == ".mp4" and got.stat().st_size > 0
    assert (src / "e.h264").exists()
    if shutil.which("ffprobe"):
        assert clips.probe_duration_s(got) == pytest.approx(1.0, abs=0.3)


def test_stop_during_wait_returns_promptly(tmp_path):
    p = clips.ClipPolicy(wait_s=30, poll_s=0.02, stable_s=0.1)
    assert wait_for_clip("e", tmp_path, tmp_path / "st", p, stop=lambda: True) is None
