"""Real-ffmpeg tests for the camera driver's frame ring and clip cutting (no Pi needed).

H.264 comes from libx264 (Annex-B, AUD per access unit, SPS/PPS repeated on every keyframe), is split into
access units and pushed through the same path picamera2's output uses (``PiCamera._on_frame``).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading

import pytest

from argus.contracts import PROTOCOL_FOR, ModuleContext
from hardware.argus import camera as cam_mod

FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
FPS = 30
NS = 1_000_000_000

pytestmark = pytest.mark.skipif(not shutil.which(FFMPEG) or not shutil.which(FFPROBE), reason="needs ffmpeg+ffprobe")


class ManualClock:
    def __init__(self, t=1_000 * NS):
        self.t = t
        self.base = t

    def now_ns(self):
        return self.t


class NullBackend:
    def start(self, on_frame):
        pass

    def stop(self):
        pass


def split_access_units(data: bytes) -> list[bytes]:
    """Split Annex-B on AUD NAL units (type 9)."""
    starts = []
    i = 0
    while (i := data.find(b"\x00\x00\x01", i)) >= 0:
        if data[i + 3] & 0x1F == 9:
            starts.append(i - 1 if i > 0 and data[i - 1] == 0 else i)
        i += 3
    starts.append(len(data))
    return [data[a:b] for a, b in zip(starts, starts[1:], strict=False)]


def is_key(au: bytes) -> bool:
    i = 0
    while (i := au.find(b"\x00\x00\x01", i)) >= 0:
        if au[i + 3] & 0x1F == 5:
            return True
        i += 3
    return False


@pytest.fixture(scope="module")
def aus():
    out = subprocess.run(
        [FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=size=160x120:rate={FPS}", "-t", "12",
         "-c:v", "libx264", "-preset", "ultrafast",
         "-x264-params", f"keyint={FPS}:min-keyint={FPS}:scenecut=0:bframes=0:aud=1:repeat-headers=1",
         "-f", "h264", "-"],
        capture_output=True, check=True,
    ).stdout
    units = split_access_units(out)
    assert len(units) == 12 * FPS
    assert [i for i, u in enumerate(units) if is_key(u)] == list(range(0, 12 * FPS, FPS))
    return units


def probe(path):
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
         "stream=codec_name,nb_read_frames:format=duration", "-of", "json", str(path)],
        capture_output=True, check=True, text=True,
    )
    j = json.loads(r.stdout)
    return j["streams"][0], float(j["format"]["duration"])


def feed(cam, clock, aus, a, b):
    for i in range(a, b):
        clock.t = clock.base + i * NS // FPS
        cam._on_frame(aus[i], is_key(aus[i]))


def setup_cam(aus, n, **opts):
    clock = ManualClock()
    cam = cam_mod.PiCamera(ModuleContext(clock, {"ffmpeg": FFMPEG, "fps": FPS, **opts}), backend=NullBackend())
    cam.start()
    feed(cam, clock, aus, 0, n)
    return cam, clock, clock.base


def test_camera_is_a_camera_protocol():
    assert isinstance(cam_mod.PiCamera(ModuleContext(ManualClock(), {})), PROTOCOL_FOR["camera"])


def test_clip_is_decodable_and_reports_actual_frames(aus, tmp_path):
    cam, clock, t0 = setup_cam(aus, 10 * FPS)
    out = tmp_path / "a" / "clip.mp4"
    info = cam.save_clip(t0 + int(2.5 * NS), t0 + 7 * NS, str(out))
    # starts at the keyframe at 2.0 s; the last frame with t <= 7.0 s is frame 210 -> frames 60..210
    assert info.t_start_ns == t0 + 2 * NS
    assert info.t_end_ns == t0 + 7 * NS
    stream, dur = probe(out)
    assert stream["codec_name"] == "h264"
    assert int(stream["nb_read_frames"]) == 151
    assert dur == pytest.approx(151 / FPS, abs=0.05)
    assert info.duration_s == pytest.approx(151 / FPS)
    assert info.bytes == out.stat().st_size > 0
    assert not (tmp_path / "a" / "clip.mp4.raw.h264").exists()


def test_two_overlapping_clips_back_to_back_are_both_full(aus, tmp_path):
    """The old CircularOutput drained the ring on dump, so the second event got a short clip."""
    cam, clock, t0 = setup_cam(aus, 11 * FPS)
    i1 = cam.save_clip(t0 + 1 * NS, t0 + 6 * NS, str(tmp_path / "c1.mp4"))
    i2 = cam.save_clip(t0 + 3 * NS, t0 + 9 * NS, str(tmp_path / "c2.mp4"))
    assert int(probe(tmp_path / "c1.mp4")[0]["nb_read_frames"]) == 5 * FPS + 1
    assert int(probe(tmp_path / "c2.mp4")[0]["nb_read_frames"]) == 6 * FPS + 1
    assert i2.t_start_ns == t0 + 3 * NS and i2.t_end_ns == t0 + 9 * NS
    assert i1.t_end_ns > i2.t_start_ns  # genuinely overlapping
    cam.save_clip(t0 + 1 * NS, t0 + 6 * NS, str(tmp_path / "c3.mp4"))  # same span again still works
    assert int(probe(tmp_path / "c3.mp4")[0]["nb_read_frames"]) == 5 * FPS + 1


def test_clip_clamped_to_ring_and_reports_it(aus, tmp_path):
    cam, clock, t0 = setup_cam(aus, 3 * FPS, max_post_wait_s=0)
    info = cam.save_clip(t0 - 10 * NS, t0 + 60 * NS, str(tmp_path / "c.mp4"))
    assert info.t_start_ns == t0  # first keyframe in the ring
    assert info.t_end_ns == t0 + (3 * FPS - 1) * NS // FPS
    assert int(probe(tmp_path / "c.mp4")[0]["nb_read_frames"]) == 3 * FPS


def test_ring_is_time_bounded_and_snaps_to_a_keyframe(aus):
    cam, clock, t0 = setup_cam(aus, 12 * FPS, ring_seconds=5)
    frames = cam._ring.snapshot(0, 1 << 62)  # start earlier than the ring -> first keyframe in the ring
    assert frames[0][1] is True
    assert frames[-1][0] - frames[0][0] <= 5 * NS
    assert len(cam._ring) <= 5 * FPS + 2


def test_save_clip_waits_for_post_roll_without_blocking_ring(aus, tmp_path):
    cam, clock, t0 = setup_cam(aus, 3 * FPS, max_post_wait_s=5)
    result = {}

    def worker():
        result["info"] = cam.save_clip(t0 + 1 * NS, t0 + 4 * NS, str(tmp_path / "w.mp4"))

    th = threading.Thread(target=worker)
    th.start()
    th.join(0.3)
    assert th.is_alive()  # waiting for frames up to t_end
    feed(cam, clock, aus, 3 * FPS, 5 * FPS)  # the ring keeps accepting frames while save_clip waits
    th.join(10)
    assert not th.is_alive()
    assert result["info"].t_end_ns == t0 + 4 * NS
    assert int(probe(tmp_path / "w.mp4")[0]["nb_read_frames"]) == 3 * FPS + 1


def test_save_clip_gives_up_waiting_and_returns_what_exists(aus, tmp_path):
    cam, clock, t0 = setup_cam(aus, 3 * FPS, max_post_wait_s=0.2)
    info = cam.save_clip(t0 + 1 * NS, t0 + 30 * NS, str(tmp_path / "s.mp4"))
    assert info.t_end_ns < t0 + 3 * NS


def test_errors_when_stopped_empty_or_no_keyframe(aus, tmp_path):
    clock = ManualClock()
    cam = cam_mod.PiCamera(ModuleContext(clock, {"max_post_wait_s": 0}), backend=NullBackend())
    with pytest.raises(RuntimeError, match="not running"):
        cam.save_clip(0, 1, str(tmp_path / "x.mp4"))
    cam.start()
    with pytest.raises(RuntimeError, match="no frames"):
        cam.save_clip(0, 1, str(tmp_path / "x.mp4"))
    cam._on_frame(aus[1], False)  # a P frame only
    with pytest.raises(RuntimeError, match="keyframe"):
        cam.save_clip(0, 1 << 62, str(tmp_path / "x.mp4"))
    assert cam.health().error_count == 3


def test_ring_accepts_buffer_types():
    ring = cam_mod.FrameRing(ManualClock(), 5)
    ring.add(memoryview(b"abc"), True)
    assert ring.snapshot(0, 1 << 62)[0][2] == b"abc"
