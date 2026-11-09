"""Live telemetry publisher + RAM recorder: non-blocking, bounded, never raises into the loops (no hardware)."""

from __future__ import annotations

import json
import os
import socket
import time
import types

import pytest

from hat import config as cfg
from hat import telemetry as T
from hat.haptics import Haptics
from hat.imu import FallImpactDetector, ImuSample
from hat.ultrasonic import ApproachDetector, MotionState
from hat.wear_state import WearState


def free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def make_src():
    return types.SimpleNamespace(
        wear_state=WearState(), detector=FallImpactDetector(), haptics=types.SimpleNamespace(last_fire={"left": 0.0, "right": 0.0}),
        camera=types.SimpleNamespace(_busy=False), imu=types.SimpleNamespace(config_losses=0, i2c_errors=0),
        approach_left=ApproachDetector("left"), approach_right=ApproachDetector("right"), motion=MotionState(),
    )


def sample(t=None, gx=1.0):
    t = time.perf_counter() if t is None else t
    return ImuSample(t, 0.01, -0.02, 1.0, gx, 0.0, 2.0)


def make_tele(tmp_path, **kw):
    kw.setdefault("port", free_udp_port())
    kw.setdefault("rec_dir", str(tmp_path / "live"))
    return T.Telemetry(make_src(), **kw)


# ---------------------------------------------------------------- publisher
def test_no_listener_nothing_breaks_and_hooks_are_fast(tmp_path):
    tele = make_tele(tmp_path)  # nobody is bound to the port
    tele.start()
    try:
        s = sample()
        t0 = time.perf_counter()
        for _ in range(5000):
            tele.imu(s)
        per_call = (time.perf_counter() - t0) / 5000
        time.sleep(0.2)  # a few publisher ticks
        assert per_call < 200e-6  # generous; measured ~1-3 us
        assert tele._thread.is_alive()
    finally:
        tele.stop()


def test_snapshot_arrives_over_udp_with_expected_fields(tmp_path):
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(2.0)
    tele = make_tele(tmp_path, port=rx.getsockname()[1])
    tele.src.wear_state.update(True, distance_mm=120, near=True)
    tele.start()
    try:
        tele.imu(sample())
        tele.event("buzz", "left", "close_and_fast")
        snap = {}
        for _ in range(10):  # first datagrams may predate our imu()
            snap = json.loads(rx.recv(65535))
            if snap.get("ax") is not None and snap.get("event"):
                break
        for k in ("t", "ax", "ay", "az", "mag", "gx", "gy", "gz", "l_dist", "r_dist", "l_close", "r_close", "gated", "moving",
                  "l_buzz", "r_buzz", "hat_on", "tof_mm", "fall", "event", "events", "config_losses", "i2c_errors",
                  "camera", "thr"):
            assert k in snap, k
        assert snap["hat_on"] is True and snap["event"]["kind"] == "buzz" and snap["event"]["side"] == "left"
        assert set(snap["fall"]) >= {"armed", "candidate", "waiting_still", "cooldown"}
        assert len(json.dumps(snap)) < 4000  # one small datagram
    finally:
        tele.stop()
        rx.close()


def test_rate_is_about_20hz(tmp_path):
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.setblocking(False)
    tele = make_tele(tmp_path, port=rx.getsockname()[1], record=False)
    tele.start()
    time.sleep(1.0)
    tele.stop()
    n = 0
    try:
        while True:
            rx.recv(65535)
            n += 1
    except BlockingIOError:
        pass
    rx.close()
    assert 12 <= n <= 28


def test_gyro_none_before_calibration_and_buzz_flag(tmp_path):
    tele = make_tele(tmp_path, record=False)
    tele.imu(ImuSample(time.perf_counter(), 0.0, 0.0, 1.0, None, None, None))
    now = time.perf_counter()
    tele.src.haptics.last_fire["right"] = now
    snap = tele.snapshot()
    assert snap["gx"] is None and snap["gyro_cal"] is False
    assert snap["r_buzz"] is True and snap["l_buzz"] is False


def test_hat_on_off_events_come_from_wear_snapshots(tmp_path):
    tele = make_tele(tmp_path, record=False)
    W = types.SimpleNamespace
    tele.wear(W(helmet_worn=False, distance_mm=None, near=False))
    tele.wear(W(helmet_worn=True, distance_mm=100, near=True))
    tele.wear(W(helmet_worn=False, distance_mm=None, near=False))
    assert [e["kind"] for e in tele.snapshot()["events"]] == ["hat_on", "hat_off"]


def test_event_list_is_capped(tmp_path):
    tele = make_tele(tmp_path, record=False)
    for i in range(25):
        tele.event("fall", None, str(i))
    evs = tele.snapshot()["events"]
    assert len(evs) == T.EVENT_KEEP and evs[-1]["text"] == "24"


# ---------------------------------------------------------------- never raises
def test_hooks_swallow_garbage(tmp_path):
    tele = make_tele(tmp_path)
    tele.imu(None)
    tele.imu(object())
    tele.us(1.0, "left", None, None, None)
    tele.wear(None)
    tele.event(None, object(), object())
    tele.i2c_error()


def test_snapshot_failures_do_not_kill_the_publisher(tmp_path):
    class Boom:
        @property
        def last_distance(self):
            raise RuntimeError("boom")

    tele = make_tele(tmp_path, record=False)
    tele.src.approach_left = Boom()
    tele.start()
    try:
        time.sleep(0.2)
        assert tele._thread.is_alive()
    finally:
        tele.stop()


def test_start_failure_is_contained(tmp_path):
    tele = make_tele(tmp_path, rec_dir="/proc/definitely/not/writable")
    tele.start()  # prints, does not raise; recording is dropped, publisher still runs
    try:
        assert tele.rec is None and tele._thread.is_alive()
    finally:
        tele.stop()


def test_create_honours_env_and_config(monkeypatch, tmp_path):
    src = make_src()
    monkeypatch.setenv("ARGUS_LIVE_TELEMETRY", "0")
    assert isinstance(T.create(src), T.NullTelemetry) and not T.create(src).enabled
    monkeypatch.setenv("ARGUS_LIVE_TELEMETRY", "1")
    monkeypatch.setenv("ARGUS_LIVE_RECORD", "0")
    monkeypatch.setenv("ARGUS_LIVE_DIR", str(tmp_path))
    t = T.create(src)
    assert t.enabled and t.rec is None
    monkeypatch.setattr(cfg, "LIVE_UDP_HOST", None)  # bad config must not raise
    monkeypatch.setattr(cfg, "LIVE_TELEMETRY", True)
    assert T.create(src) is not None
    n = T.NullTelemetry()
    n.imu(1), n.us(1, 2, 3, 4, 5), n.wear(1), n.event("x"), n.i2c_error(), n.start(), n.stop()


def test_defaults_are_on():
    assert cfg.LIVE_TELEMETRY is True and cfg.LIVE_RECORD is True
    assert cfg.LIVE_REC_DIR.startswith("/dev/shm/")


# ---------------------------------------------------------------- recorder
def test_full_queue_drops_instead_of_blocking(tmp_path):
    rec = T.Recorder(tmp_path / "r", queue_size=10)  # writer not started: nothing drains
    t0 = time.perf_counter()
    for i in range(1000):
        rec.put(("i", float(i), 0, 0, 1, None, None, None))
    assert time.perf_counter() - t0 < 0.5
    assert len(rec.q) == 10 and rec.dropped == 990


def test_rows_land_in_csv_files(tmp_path):
    tele = make_tele(tmp_path, port=free_udp_port(), udp=False)
    tele.start()
    try:
        t = time.perf_counter()
        tele.imu(ImuSample(t, 0.1, 0.2, 0.9, None, None, None))
        tele.imu(ImuSample(t + 0.005, 0.1, 0.2, 0.9, 1.0, 2.0, 3.0))
        det = tele.src.approach_left
        det.update(t, 1.5)
        tele.us(t, "left", 1.52, det, tele.src.motion)
        tele.event("buzz", "left", "close_and_fast")
        tele.src.haptics.last_fire["left"] = t
        tele._note_buzz_pulses(t)
        tele.wear(types.SimpleNamespace(helmet_worn=True, distance_mm=90, near=True))
        deadline = time.time() + 3
        while tele.rec.written < 8 and time.time() < deadline:
            time.sleep(0.05)
    finally:
        tele.stop()
    d = tmp_path / "live"
    imu = next(d.glob("imu-*.csv")).read_text().splitlines()
    assert imu[0] == "t,ax,ay,az,gx,gy,gz" and imu[1].endswith(",,,") and len(imu) == 3
    assert abs(float(imu[1].split(",")[0]) - time.time()) < 10  # wall clock
    us = next(d.glob("us-*.csv")).read_text().splitlines()
    assert us[0].startswith("t,side,raw_m,tracked_m,closing_mps,gated,moving") and ",left,1.5200,1.5000," in us[1]
    ev = next(d.glob("events-*.csv")).read_text()
    for kind in ("meta", "buzz,left,close_and_fast", "buzz_pulse,left", "wear,,worn=1 mm=90 near=1", "hat_on"):
        assert kind in ev, kind


def _rows(rec, n):
    for i in range(n):
        rec.put(("i", float(i), 0.1, 0.2, 0.3, 1.0, 2.0, 3.0))


def test_rotation_by_segment_and_total_size(tmp_path):
    d = tmp_path / "r"
    rec = T.Recorder(d, queue_size=100000, segment_mb=0.01, max_mb=0.05, cleanup_every_s=0.0)
    assert rec.start()
    for _ in range(10):
        _rows(rec, 3000)
        time.sleep(0.15)  # let the writer drain + clean between bursts
    rec.stop()
    rec.cleanup()
    files = sorted(d.glob("imu-*.csv"))
    total = sum(f.stat().st_size for f in files)
    assert len(files) >= 2, "segments rolled"
    assert total <= 0.05 * 1024 * 1024 + 1, f"cap exceeded: {total}"
    assert rec.dropped == 0 and files[-1].name.endswith("-0001.csv") is False  # oldest segments were deleted


def test_old_files_are_deleted_by_age(tmp_path):
    d = tmp_path / "r"
    d.mkdir()
    old, fresh = d / "imu-old-0001.csv", d / "imu-new-0001.csv"
    old.write_text("x"), fresh.write_text("x")
    past = time.time() - 4000
    os.utime(old, (past, past))
    rec = T.Recorder(d, keep_s=1800)
    rec.cleanup()
    assert not old.exists() and fresh.exists()


def test_write_errors_drop_rows_and_never_raise(tmp_path, monkeypatch):
    rec = T.Recorder(tmp_path / "r", segment_mb=1)
    assert rec.start()

    def bad(self, lines):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(T._Stream, "write", bad)
    _rows(rec, 50)
    deadline = time.time() + 2
    while rec.write_errors == 0 and time.time() < deadline:
        time.sleep(0.05)
    rec.stop()
    assert rec.write_errors >= 1 and rec.dropped >= 1


# ---------------------------------------------------------------- hooks in the real classes
def test_haptics_records_last_fire_without_other_changes():
    h = Haptics(dry_run=True)
    try:
        assert h.last_fire == {"left": 0.0, "right": 0.0}
        h._fire("left", 47)
        assert h.last_fire["left"] > 0 and h.last_fire["right"] == 0.0
        h._fire("both", 47)
        assert h.last_fire["right"] > 0
    finally:
        h.close()


def test_detector_status_is_read_only_view():
    d = FallImpactDetector()
    st = d.status(time.perf_counter())
    assert st == {"armed": True, "candidate": False, "waiting_still": False, "cooldown": False, "state": "idle"}
    d._state = "wait_still"
    assert d.status(0.0)["waiting_still"] and d.status(0.0)["candidate"]


def test_hat_main_wires_the_hooks():
    """hat.main needs gpiozero (Pi only): check the wiring textually on a laptop."""
    src = open(os.path.join(os.path.dirname(T.__file__), "main.py")).read()
    for needle in ("telemetry.create(self)", "self.tele.imu(sample)", "self.tele.us(t0,", "self.tele.wear(snap)",
                   "self.tele.start()", "self.tele.stop()", 'self.tele.event("config_lost"', "self.tele.i2c_error()"):
        assert needle in src, needle
