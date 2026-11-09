"""Launching their hat.main (a fake one here): pass-through, [tof] parsing, restart with backoff, clean shutdown."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
import yaml

from argus.bridge.launcher import HatLauncher, TofLine, parse_tof, parse_tof_all

from .conftest import Hat, T0, make_bridge, make_cfg, queued


def wait(cond, timeout=15.0, what="condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


# ---------------------------------------------------------------- parsing
def test_exact_format_from_their_main():
    t = parse_tof("[tof] HELMET ON  mm=59 near=True\n")
    assert t == TofLine(True, "59", "True")
    assert parse_tof("[tof] HELMET OFF  mm=None near=False") == TofLine(False, "None", "False")


def test_glued_and_carriage_return_fragments():
    glued = "US L=0.31m R=--\r US L=0.30m[tof] HELMET OFF  mm=None near=False\rUS L=0.29m R=1.2\r[tof] HELMET ON  mm=59 near=True\r"
    assert [t.on for t in parse_tof_all(glued)] == [False, True]
    assert parse_tof("noise[tof] HELMET ON  mm=61 near=True trailing") == TofLine(True, "61", "True")
    assert parse_tof("[tof] unavailable — fall wear-gate will suppress until ToF is up") is None
    assert parse_tof("HELMET ON without the prefix") is None


# ---------------------------------------------------------------- the child
HAT_MAIN = textwrap.dedent("""
    import json, os, sys, time
    d = os.environ["FAKE_HAT_DIR"]
    n = len(open(os.path.join(d, "starts.log")).read().splitlines()) if os.path.exists(os.path.join(d, "starts.log")) else 0
    open(os.path.join(d, "starts.log"), "a").write(f"{os.getpid()}\\n")
    print("MPU WHO_AM_I=0x68", flush=True)
    sys.stdout.write("US 0.3m\\r[tof] HELMET ON  mm=59 near=True\\rUS 0.4m\\n"); sys.stdout.flush()
    print("[error] something on stderr", file=sys.stderr, flush=True)
    if os.environ.get("FAKE_HAT_MODE") == "crash" and n < 2:
        sys.exit(3)
    open(os.path.join(d, "ready"), "w").write("1")
    if os.environ.get("FAKE_HAT_MODE") == "hang":
        import subprocess
        g = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        open(os.path.join(d, "grandchild"), "w").write(str(g.pid))
    while True:
        time.sleep(0.2)
""")


@pytest.fixture
def fake_hat(tmp_path, monkeypatch):
    d = tmp_path / "argus"
    (d / "hat").mkdir(parents=True)
    (d / "hat" / "__init__.py").write_text("")
    (d / "hat" / "main.py").write_text(HAT_MAIN)
    monkeypatch.setenv("FAKE_HAT_DIR", str(d))
    return d


def test_output_passthrough_tof_callback_and_clean_shutdown(fake_hat):
    out, tofs = [], []
    ln = HatLauncher([sys.executable, "-m", "hat.main"], fake_hat, tofs.append, out=out.append)
    ln.start()
    wait(lambda: (fake_hat / "ready").exists() and tofs, what="child ready")
    pid = int((fake_hat / "starts.log").read_text().split()[0])
    ln.stop()
    text = "".join(out)
    assert "MPU WHO_AM_I=0x68" in text and "[error] something on stderr" in text  # stdout AND stderr, unchanged
    assert "US 0.3m\r[tof] HELMET ON  mm=59 near=True\rUS 0.4m\n" in text
    assert tofs == [TofLine(True, "59", "True")]
    assert not alive(pid)


def test_child_exit_restarts_with_backoff(fake_hat, monkeypatch):
    monkeypatch.setenv("FAKE_HAT_MODE", "crash")
    ln = HatLauncher([sys.executable, "-m", "hat.main"], fake_hat, lambda t: None, out=lambda s: None,
                     backoff_s=(0.1, 0.4))
    ln.start()
    wait(lambda: (fake_hat / "ready").exists(), what="third start staying up")
    ln.stop()
    assert ln.exits[:2] == [3, 3] and ln.starts == 3


def test_shutdown_kills_the_whole_process_group(fake_hat, monkeypatch):
    monkeypatch.setenv("FAKE_HAT_MODE", "hang")
    ln = HatLauncher([sys.executable, "-m", "hat.main"], fake_hat, lambda t: None, out=lambda s: None)
    ln.start()
    wait(lambda: (fake_hat / "grandchild").exists(), what="grandchild")
    gpid = int((fake_hat / "grandchild").read_text())
    assert alive(gpid)
    ln.stop()
    wait(lambda: not alive(gpid), 5, "grandchild gone")


def test_unstartable_command_does_not_crash_the_bridge(tmp_path):
    ln = HatLauncher(["/no/such/python", "-m", "hat.main"], tmp_path, lambda t: None, out=lambda s: None,
                     backoff_s=(0.05, 0.1))
    ln.start()
    time.sleep(0.3)
    ln.stop()


# ---------------------------------------------------------------- the whole bridge with a launched child
def test_bridge_launches_hat_and_air_children_and_hat_off_becomes_hat_removed(fake_hat, tmp_path):
    cfg = make_cfg(tmp_path, python=sys.executable, argus_dir=str(fake_hat), air_launch=True,
                   air_python=sys.executable)
    (fake_hat / "pi_sensors").mkdir()
    (fake_hat / "pi_sensors" / "argus_sensors").mkdir()
    (fake_hat / "pi_sensors" / "argus_sensors" / "__init__.py").write_text("")
    (fake_hat / "pi_sensors" / "argus_sensors" / "__main__.py").write_text(
        "import sys, time\nopen('argv.txt','w').write(' '.join(sys.argv[1:]))\ntime.sleep(60)\n")
    b = make_bridge(cfg, hat_on=None)
    b.do_launch = True
    b.start()
    try:
        wait(lambda: b.tof_lines >= 1, what="[tof] ON from the child")
        assert b._hat_on is True
        wait(lambda: (fake_hat / "pi_sensors" / "argv.txt").exists(), what="air child")
        argv = (fake_hat / "pi_sensors" / "argv.txt").read_text()
        assert argv == "--sensors scd41 --scd-bus 1 --log-dir logs"
        assert "--tof-bus" not in argv and "--bus" not in argv
    finally:
        b.stop()


def test_air_args_may_never_touch_the_tof_bus():
    from argus.config import ConfigError, parse_config
    raw = yaml.safe_load((Path(__file__).parents[2] / "config.yaml").read_text())
    raw["bridge"]["air_args"] = ["--sensors", "scd41", "--tof-bus", "3"]
    with pytest.raises(ConfigError):
        parse_config(raw)


def test_tof_log_mode_feeds_hat_removed(tmp_path):
    cfg = make_cfg(tmp_path)
    log = tmp_path / "hat.log"
    log.write_text("old line [tof] HELMET OFF  mm=None near=False\n")  # before we started: ignored
    from .conftest import Clock
    clock = Clock()
    b = make_bridge(cfg, clock, hat_on=None, tof_log=log)
    b.step()
    assert b.tof_lines == 0
    with log.open("a") as f:
        f.write("US 0.3m\r[tof] HELMET OFF  mm=None near=False\r")
        f.write("\n")
    b.step()
    assert b.tof_lines == 1
    clock.advance(1)
    b.step()
    b.futures[-1].result(timeout=5)
    assert [d["type"] for d in queued(cfg).values()] == ["hat_removed"]


# ---------------------------------------------------------------- the CLI process
def test_cli_run_sigterm_flushes_and_stops_the_child(fake_hat, tmp_path):
    cfg = make_cfg(tmp_path, python=sys.executable, argus_dir=str(fake_hat), air_launch=False, launch=True)
    raw = cfg.model_dump()
    cfgfile = tmp_path / "bridge.yaml"
    cfgfile.write_text(yaml.safe_dump(raw))
    hat = Hat(cfg)
    hat.events.write_text("")
    env = dict(os.environ, FAKE_HAT_DIR=str(fake_hat), PYTHONUNBUFFERED="1")
    p = subprocess.Popen([sys.executable, "-c", "from argus.cli import app; app()", "bridge", "run", "--config",
                          str(cfgfile), "--no-uploader"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True)
    try:
        wait(lambda: (fake_hat / "ready").exists(), 20, "child started by the CLI")
        pid = int((fake_hat / "starts.log").read_text().split()[0])
        ev = hat.fall(T0)
        hat.mp4(ev["event_id"])
        wait(lambda: ev["event_id"] in queued(cfg), 20, "event queued by the CLI process")
        p.send_signal(signal.SIGTERM)
        out, _ = p.communicate(timeout=30)
    finally:
        if p.poll() is None:
            p.kill()
    assert p.returncode == 0, out
    assert "MPU WHO_AM_I=0x68" in out  # their console output reached our stdout
    wait(lambda: not alive(pid), 5, "hat.main child gone after SIGTERM")
    state = json.loads((Path(cfg.bridge.state_dir) / "bridge_state.json").read_text())
    assert state["offset"] == hat.events.stat().st_size and state["pending"] == {}
