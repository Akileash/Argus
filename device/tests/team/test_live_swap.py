"""Fake teammates inside a real, threaded supervisor (spec §5.4): hot-swap over the control channel, automatic
fallback on a dead / garbage / instantly-exiting script. Real subprocesses, real clock."""

from __future__ import annotations

import os
import signal
import tempfile
import time

import pytest

from argus import control
from argus.bus import SampleBus
from argus.config import load_config
from argus.contracts import MonotonicClock
from argus.supervisor import Supervisor

from .conftest import FAKES, team


def wait_for(pred, timeout=8.0, what="condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if v := pred():
            return v
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


def start_sup(cfg_path, **kw):
    events: list[dict] = []
    sup = Supervisor(load_config(cfg_path), SampleBus(), MonotonicClock(), on_event=events.append,
                     scenario="idle", config_path=cfg_path, **kw)
    sup.start()
    sup.events = events  # type: ignore[attr-defined]
    return sup


def max_gap_ms(samples):
    ts = [s.t_ns for s in samples]
    return max((b - a) / 1e6 for a, b in zip(ts, ts[1:], strict=False)) if len(ts) > 1 else float("inf")


def n(sup, stream):
    return len(sup.bus.window(stream, 0, 1 << 62))


def test_reload_to_team_and_back_over_control_socket(cfg_file):
    cfg_path = cfg_file()
    sup = start_sup(cfg_path)
    sock = os.path.join(tempfile.mkdtemp(prefix="argt"), "c.sock")  # AF_UNIX paths must be short
    srv = control.ControlServer(sup, sock)
    srv.start()
    try:
        wait_for(lambda: n(sup, "imu") > 100 and n(sup, "range") > 10, what="mock data")
        env_before, tof_before = n(sup, "env"), n(sup, "tof")
        cam = sup.module("camera")

        # 1. config says imu + range are team now; `reload` re-reads it (this is `argus-cli reload`)
        cfg_file({"imu": team("imu"), "range": team("range")})  # rewrites the same config.yaml
        for m in ("imu", "range"):
            r = control.send({"cmd": "reload", "module": m}, sock)
            assert r["ok"] and r["impl"] == "team", r
        imu_mark = sup.clock.now_ns()
        wait_for(lambda: len(sup.bus.window("imu", imu_mark, 1 << 62)) > 200, what="team imu samples")
        team_imu = sup.bus.window("imu", imu_mark, 1 << 62)
        assert all(s.az == pytest.approx(1.0, abs=1e-6) and s.ax == pytest.approx(0.02, abs=1e-6)
                   and s.ay == pytest.approx(-0.03, abs=1e-6) for s in team_imu), "units/axes wrong end to end"
        assert {s.sensor_id for s in sup.bus.window("range", imu_mark, 1 << 62)} == {"R2", "R3"}
        st = sup.status()
        assert st["imu"]["impl"] == "team" and st["range"]["impl"] == "team"
        assert st["imu"]["health"]["ok"] and not st["imu"]["on_fallback"]

        # other modules were never touched and their ring buffers kept filling
        assert sup.module("camera") is cam
        assert st["env"]["impl"] == "mock" and st["hat_on"]["impl"] == "mock"
        wait_for(lambda: n(sup, "tof") > tof_before + 5, what="hat_on still flowing")
        assert n(sup, "env") >= env_before

        # 2. and back to mock
        back_mark = sup.clock.now_ns()
        for m in ("imu", "range"):
            r = control.send({"cmd": "reload", "module": m, "impl": "mock"}, sock)
            assert r["ok"] and r["impl"] == "mock", r
        wait_for(lambda: len(sup.bus.window("imu", back_mark, 1 << 62)) > 200, what="mock imu again")
        assert any(abs(s.ax - 0.02) > 1e-6 for s in sup.bus.window("imu", back_mark + 100_000_000, 1 << 62))
        # imu is an I2C module: new impl starts before the old stops, so the buffer has no hole at either swap
        imu_all = sup.bus.window("imu", 0, 1 << 62)
        assert max_gap_ms(imu_all) < 300, f"imu ring buffer hole {max_gap_ms(imu_all):.0f} ms"
        assert sup.events == [], f"unexpected fallback events {sup.events}"
    finally:
        srv.stop()
        sup.stop()


def test_killed_script_falls_back_and_emits_module_fallback(cfg_file):
    sup = start_sup(cfg_file({"imu": team("imu")}))
    try:
        wait_for(lambda: n(sup, "imu") > 100, what="team imu running")
        assert sup.status()["imu"]["impl"] == "team"
        os.killpg(sup.module("imu")._proc.pid, signal.SIGKILL)  # the teammate's script dies
        ev = wait_for(lambda: next((e for e in sup.events if e["type"] == "module_fallback"), None),
                      what="module_fallback")
        assert ev["module"] == "imu" and ev["from_impl"] == "team" and ev["to_impl"] == "mock"
        assert "exited" in ev["reason"] or "stale" in ev["reason"]
        assert sup.status()["imu"]["impl"] == "mock"
        mark = sup.clock.now_ns()
        wait_for(lambda: len(sup.bus.window("imu", mark, 1 << 62)) > 100, what="data from fallback")
    finally:
        sup.stop()


def fake(script, *args):
    return ["python", str(FAKES / script), *args]


@pytest.mark.parametrize("module,mapping,command,why", [
    ("imu", "imu", fake("fake_imu.py", "--garbage"), "stale"),
    ("range", "range", fake("fake_range.py", "--garbage"), "stale"),
    ("range", "range", fake("fake_range.py", "--exit-now"), "exited with code 0"),
    ("imu", "imu", fake("fake_imu.py", "--crash-after", "20"), "exited with code 3"),
])
def test_bad_scripts_fall_back_without_crashing_the_supervisor(cfg_file, module, mapping, command, why):
    sup = start_sup(cfg_file({module: team(mapping, command=command)}))
    try:
        ev = wait_for(lambda: next((e for e in sup.events if e["type"] == "module_fallback"), None),
                      what="module_fallback")
        assert ev["module"] == module and ev["to_impl"] == "mock", ev
        assert why in ev["reason"], ev["reason"]
        stream = "range" if module == "range" else "imu"
        mark = sup.clock.now_ns()
        wait_for(lambda: len(sup.bus.window(stream, mark, 1 << 62)) > 10, what="fallback delivers data")
        assert n(sup, "tof") > 0  # others unaffected
    finally:
        sup.stop()
