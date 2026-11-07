"""Team template behaviour (M1.4) and CLI/control-channel smoke tests (M1.1)."""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import time

import pytest
from typer.testing import CliRunner

from argus import control
from argus.bus import SampleBus
from argus.cli import app
from argus.contracts import MODULE_NAMES, ImuSample, ModuleContext
from argus.supervisor import ModuleLoadError, Supervisor, load_impl
from hardware.mock.clock import VirtualClock
from hardware.team._adapter_base import StreamAdapter, SubprocessAdapter

from .test_supervisor import make_cfg

runner = CliRunner()


# ------------------------------------------------------------------ team templates
@pytest.mark.parametrize("module", MODULE_NAMES)
def test_team_template_raises_clear_error_so_supervisor_falls_back(module):
    adapter = importlib.import_module(f"hardware.team.{module}.adapter")
    with pytest.raises(NotImplementedError, match=f"team impl for {module} not pasted yet"):
        adapter.create(ModuleContext(VirtualClock()))
    with pytest.raises(ModuleLoadError, match="not pasted yet"):
        load_impl(module, "team", ModuleContext(VirtualClock()))


@pytest.mark.parametrize("module", MODULE_NAMES)
def test_team_readme_documents_flow(module):
    from pathlib import Path

    text = (Path(importlib.import_module(f"hardware.team.{module}").__file__).parent / "README.md").read_text()
    assert "Wiring" in text and "subprocess" in text and f"--module {module} --impl team" in text


def test_thread_pattern_adapter_drains_and_reports_health():
    class Fake(StreamAdapter):
        def run(self, emit):
            for i in range(5):
                emit(ImuSample(self.now(), 0, 0, 1, 0, 0, i))
            while not self.stopping:
                time.sleep(0.01)

    a = Fake(ModuleContext(VirtualClock()))
    a.start()
    time.sleep(0.1)
    assert [s.gz for s in a.read()] == [0, 1, 2, 3, 4] and a.read() == []
    assert a.health().ok
    a.stop()
    assert not a.health().ok


def test_subprocess_pattern_parses_blocking_print_loop():
    """Spec §5.5: a team script that is a blocking ``while True: print(...)`` runs unchanged."""

    class Sub(SubprocessAdapter):
        command = (sys.executable, "-u", "-c",
                   "import time\nfor i in range(3):\n    print(f'ax=0.0 ay=0.0 az=1.0 gx=0 gy=0 gz={i}')\ntime.sleep(5)")

        def parse_line(self, line, t_ns):
            k = dict(p.split("=") for p in line.split())
            return [ImuSample(t_ns, *(float(k[n]) for n in ("ax", "ay", "az", "gx", "gy", "gz")))]

    a = Sub(ModuleContext(VirtualClock()))
    a.start()
    deadline = time.monotonic() + 3
    got: list = []
    while len(got) < 3 and time.monotonic() < deadline:
        got += a.read()
        time.sleep(0.02)
    a.stop()
    assert [s.gz for s in got] == [0.0, 1.0, 2.0]


def test_crashing_team_code_is_contained_and_counted():
    class Boom(StreamAdapter):
        def run(self, emit):
            raise ValueError("their script blew up")

    a = Boom(ModuleContext(VirtualClock()))
    a.start()
    time.sleep(0.2)
    assert a.read() == [] and a.health().error_count >= 1 and "blew up" in a.health().note
    a.stop()


# ------------------------------------------------------------------ CLI
def test_cli_scenario_list_and_run(tmp_path):
    r = runner.invoke(app, ["scenario", "--list"])
    assert r.exit_code == 0 and "hat_drop_1m" in r.output and "board_swing_rear" in r.output
    rec = tmp_path / "out.jsonl"
    r = runner.invoke(app, ["scenario", "walk_backward_wall", "--seconds", "1.5", "--record", str(rec)])
    assert r.exit_code == 0 and "expected: no events" in r.output and rec.exists()
    r = runner.invoke(app, ["replay", str(rec), "--speed", "20"])
    assert r.exit_code == 0 and "imu=" in r.output
    assert runner.invoke(app, ["scenario", "nope"]).exit_code == 1


def test_cli_validates_module_and_missing_instance():
    assert runner.invoke(app, ["reload", "lidar"]).exit_code == 1
    os.environ["ARGUS_CONTROL_SOCK"] = os.path.join(tempfile.gettempdir(), "argus-test-missing.sock")
    try:
        r = runner.invoke(app, ["reload", "imu"])
        assert r.exit_code == 1 and "no running" in (r.output + str(r.exception))
    finally:
        del os.environ["ARGUS_CONTROL_SOCK"]


def test_cli_buzz_and_bench_haptics():
    assert runner.invoke(app, ["buzz", "both", "--seconds", "0.01"]).exit_code == 0
    assert runner.invoke(app, ["buzz", "left"]).exit_code == 1
    r = runner.invoke(app, ["test", "haptics"])
    assert r.exit_code == 0 and "zero-intensity self-test" in r.output and "SKIP" in r.output


def test_cli_bench_imu_checklist_on_mock():
    r = runner.invoke(app, ["test", "imu"])
    assert r.exit_code == 0, r.output
    assert "stationary |a| ~ 1 g" in r.output and "[PASS]" in r.output and "result: PASS" in r.output


def test_cli_bench_camera_on_mock():
    r = runner.invoke(app, ["test", "camera"])
    assert r.exit_code == 0, r.output
    assert "save_clip writes an MP4" in r.output


def test_cli_run_for_a_second():
    r = runner.invoke(app, ["run", "--duration", "1.2", "--no-control", "--scenario", "board_swing_rear"])
    assert r.exit_code == 0 and "buffered" in r.output


def test_cli_shadow_requires_fallback():
    r = runner.invoke(app, ["shadow", "imu"])
    assert r.exit_code == 1
    r = runner.invoke(app, ["shadow", "imu", "--b", "mock", "--seconds", "0.5"])
    assert r.exit_code == 0 and "diff log" in r.output


# ------------------------------------------------------------------ control channel
def test_control_channel_reload_status_fault():
    path = os.path.join(tempfile.gettempdir(), f"argus-test-{os.getpid()}.sock")
    sup = Supervisor(make_cfg(), SampleBus(), VirtualClock(), scenario="idle")
    sup.start(run_threads=False)
    srv = control.ControlServer(sup, path)
    srv.start()
    try:
        r = control.send({"cmd": "reload", "module": "imu"}, path)
        assert r["ok"] and r["seconds"] < 2.0 and r["impl"] == "mock"
        assert control.send({"cmd": "status"}, path)["status"]["imu"]["impl"] == "mock"
        assert control.send({"cmd": "fault", "module": "imu", "kind": "kill"}, path)["ok"]
        sup.check_once("imu")
        assert json.loads(json.dumps(control.send({"cmd": "status"}, path)["status"]["imu"]))["on_fallback"]
        bad = control.send({"cmd": "reload", "module": "imu", "impl": "team"}, path)
        assert not bad["ok"] and "not pasted yet" in bad["error"]
        assert not control.send({"cmd": "dance"}, path)["ok"]
    finally:
        srv.stop()
        sup.stop()


def test_control_server_refuses_live_socket_and_replaces_stale():
    import socket

    path = os.path.join(tempfile.gettempdir(), f"argus-busy-{os.getpid()}.sock")
    sup = Supervisor(make_cfg(), SampleBus(), VirtualClock(), scenario="idle")
    sup.start(run_threads=False)
    first = control.ControlServer(sup, path)
    first.start()
    try:
        with pytest.raises(control.ControlSocketBusy):
            control.ControlServer(sup, path)
        assert control.send({"cmd": "status"}, path)["ok"]  # the live one still owns the socket
    finally:
        first.stop()
    s = socket.socket(socket.AF_UNIX)
    s.bind(path)  # stale: file exists, nobody listening
    s.close()
    second = control.ControlServer(sup, path)  # replaces the dead socket
    second._srv.server_close()  # never started serving, so stop() would block in shutdown()
    sup.stop()


def test_run_refuses_when_another_instance_owns_the_socket(monkeypatch):
    path = os.path.join(tempfile.gettempdir(), f"argus-run-busy-{os.getpid()}.sock")
    monkeypatch.setenv("ARGUS_CONTROL_SOCK", path)
    sup = Supervisor(make_cfg(), SampleBus(), VirtualClock(), scenario="idle")
    sup.start(run_threads=False)
    first = control.ControlServer(sup, path)
    first.start()
    try:
        r = CliRunner().invoke(app, ["run", "--duration", "2"])
        assert r.exit_code == 1 and "already listening" in r.output
        assert control.send({"cmd": "status"}, path)["ok"]
    finally:
        first.stop()
        sup.stop()


def test_scenario_prints_configured_sensors():
    r = CliRunner().invoke(app, ["scenario", "idle", "--seconds", "0.1"])
    header = next(line for line in r.output.splitlines() if "ToF[mm]" in line)
    assert "R2" in header and "R3" in header and "R1" not in header and "R4" not in header
