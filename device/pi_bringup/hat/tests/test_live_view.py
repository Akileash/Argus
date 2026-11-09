"""Live viewer: rendering from fake snapshots (80x24), no-data banner, plain mode, terminal restore."""

from __future__ import annotations

import io
import json
import os
import pty
import select
import signal
import socket
import struct
import subprocess
import sys
import termios
import threading
import time
import fcntl
from pathlib import Path

from hat.tools import live_view as lv

NOW = 1_800_000_000.0


def snap(**over):
    d = {
        "v": 1, "boot": "b1", "seq": 7, "t": NOW, "up": 42.0,
        "ax": 0.01, "ay": -0.02, "az": 1.0, "mag": 1.0, "gx": 1.0, "gy": 2.0, "gz": 3.0,
        "l_raw": 2.5, "l_dist": 2.5, "l_close": 0.0, "r_raw": 2.5, "r_dist": 2.5, "r_close": 0.0,
        "l_buzz": False, "r_buzz": False, "gated": False, "moving": False,
        "hat_on": True, "tof_mm": 120, "tof_near": True,
        "fall": {"armed": True, "candidate": False, "waiting_still": False, "cooldown": False},
        "config_losses": 0, "i2c_errors": 0, "camera": False, "events": [],
        "thr": {"impact_g": 3.0, "freefall_g": 0.45, "gyro_gate_dps": 40.0, "warn_m": 1.8, "min_closing_mps": 1.0,
                "max_range_m": 3.0, "buzz_on_s": 0.2},
    }
    d.update(over)
    return d


def view(**over):
    st = lv.ViewState()
    assert st.feed(json.dumps(snap(**over)).encode(), NOW)
    return st


def styles_of(lines, needle):
    """Styles of the segments whose text contains `needle`."""
    return {st for ln in lines for t, st in ln if needle in t}


def test_every_frame_is_exactly_80x24():
    cases = [view(), view(l_buzz=True, r_buzz=True, hat_on=False), view(gx=None, gy=None, gz=None, l_dist=None, r_dist=None,
                                                                         l_raw=None, r_raw=None, mag=None, ax=None,
                                                                         ay=None, az=None, hat_on=None, fall=None, tof_mm=None),
             lv.ViewState()]
    for st in cases:
        for tick in (0, 1):
            frame = lv.render(st, 80, 24, NOW, tick)
            text = lv.to_text(frame)
            assert len(text) == 24 and all(len(t) == 80 for t in text), [len(t) for t in text]
            assert all(ch.isascii() for t in text for ch in t)


def test_small_and_big_terminals_do_not_crash():
    for w, h in ((40, 12), (20, 10), (200, 60)):
        frame = lv.render(view(), w, h, NOW)
        assert len(frame) == h and all(len("".join(t for t, _ in ln)) == w for ln in frame)


def test_no_data_banner():
    st = lv.ViewState()
    text = "\n".join(lv.to_text(lv.render(st, 80, 24, NOW)))
    assert "NO DATA: is hat.main running?" in text
    assert "ax" not in text.split("events")[0].replace("max", "")  # no stale numbers
    # data then silence -> banner again, with the age
    st = view()
    assert "NO DATA" not in "\n".join(lv.to_text(lv.render(st, 80, 24, NOW + 0.5)))
    late = "\n".join(lv.to_text(lv.render(st, 80, 24, NOW + 10)))
    assert "NO DATA: is hat.main running?" in late and "last data 10s ago" in late


def test_numbers_are_shown():
    text = "\n".join(lv.to_text(lv.render(view(ax=0.123, l_dist=1.23, l_close=0.45), 80, 24, NOW)))
    for s in ("+0.123", "1.23 m", "+0.45 m/s", "HAT ON", "120mm", "FRONT", "REAR-L", "REAR-R", "armed"):
        assert s in text, s


def test_red_only_past_real_thresholds():
    calm = lv.render(view(), 80, 24, NOW)
    for bad in ("alert", "red"):
        assert not [1 for ln in calm for t, st in ln if st == bad and t.strip()], bad
    # |a| over the impact threshold
    assert "alert" in styles_of(lv.render(view(mag=3.5), 80, 24, NOW), "3.500")
    assert "alert" not in styles_of(lv.render(view(mag=2.9), 80, 24, NOW), "2.900")
    # free-fall (under freefall_g)
    assert "red" in styles_of(lv.render(view(mag=0.2), 80, 24, NOW), "0.200")
    # gyro over the gate (the threshold comes from the snapshot, i.e. the running hat.main)
    hot = lv.render(view(gx=50.0, gy=0.0, gz=0.0), 80, 24, NOW)
    assert "alert" in styles_of(hot, "50.0 dps")
    cool = lv.render(view(gx=50.0, gy=0.0, gz=0.0, thr={**snap()["thr"], "gyro_gate_dps": 90.0}), 80, 24, NOW)
    assert "alert" not in styles_of(cool, "50.0 dps")
    # range under the warn distance
    near = lv.render(view(l_dist=1.2, l_raw=1.2), 80, 24, NOW)
    assert "red" in styles_of(near, "1.20 m")
    assert "red" not in styles_of(lv.render(view(l_dist=1.9, l_raw=1.9), 80, 24, NOW), "1.90 m")


def test_motors_flash_only_while_buzzing():
    idle = lv.render(view(), 80, 24, NOW)
    assert "(L)" in "".join(lv.to_text(idle)) and not styles_of(idle, "L") & {"flash"}
    for tick in (0, 1):
        on = lv.render(view(l_buzz=True), 80, 24, NOW, tick)
        flashing = [t for ln in on for t, st in ln if st == "flash"]
        assert any("L" in t for t in flashing) and not any("R#" in t or "*R*" in t for t in flashing)


def test_hat_indicator_states():
    for on, label in ((True, "HAT ON"), (False, "HAT OFF"), (None, "HAT ?")):
        assert label in "\n".join(lv.to_text(lv.render(view(hat_on=on), 80, 24, NOW)))


def test_beam_bar_grows_with_distance():
    def beam_len(d):
        text = lv.to_text(lv.render(view(l_dist=d, l_raw=d), 80, 24, NOW))
        col = text[8].index("v") if "v" in text[8] else None
        return sum(1 for row in text[7:13] for ch in row[:38] if ch in "|v")  # both beams' filled cells
    assert beam_len(0.5) < beam_len(2.9)


def test_event_log_keeps_last_ten_and_dedups():
    st = lv.ViewState()
    evs = [{"id": i, "t": NOW + i, "kind": "buzz", "side": "left", "text": f"reason{i}"} for i in range(1, 16)]
    st.feed(json.dumps(snap(events=evs[:10])).encode(), NOW)
    st.feed(json.dumps(snap(events=evs[5:15])).encode(), NOW + 0.1)
    st.feed(json.dumps(snap(events=evs[5:15])).encode(), NOW + 0.2)
    assert [e["id"] for e in st.events] == list(range(6, 16))
    text = "\n".join(lv.to_text(lv.render(st, 80, 24, NOW + 0.3)))
    assert "reason15" in text and "reason6" in text and "reason5" not in text
    # a restarted hat.main (new boot id) starts its ids over, nothing is lost
    st.feed(json.dumps(snap(boot="b2", events=[{"id": 1, "t": NOW, "kind": "fall", "side": None, "text": "peak 4g"}])).encode(), NOW + 1)
    assert st.events[-1]["kind"] == "fall"


def test_event_kinds_are_listed():
    kinds = ["buzz", "near_miss", "fall", "hat_on", "hat_off", "config_lost"]
    evs = [{"id": i + 1, "t": NOW, "kind": k, "side": "left" if k in ("buzz", "near_miss") else None, "text": "x"}
           for i, k in enumerate(kinds)]
    text = "\n".join(lv.to_text(lv.render(view(events=evs), 80, 24, NOW)))
    for k in kinds:
        assert k.upper() in text


def test_garbage_datagrams_are_ignored():
    st = lv.ViewState()
    for junk in (b"", b"\xff\xfe", b"[1,2]", b"{bad", b"123"):
        assert st.feed(junk, NOW) is False
    assert st.snap is None


def test_gyro_not_calibrated_is_said_plainly():
    text = "\n".join(lv.to_text(lv.render(view(gx=None, gy=None, gz=None), 80, 24, NOW)))
    assert "not calibrated" in text


def test_ansi_output_starts_at_home_and_resets_styles():
    out = lv.to_ansi(lv.render(view(mag=4.0), 80, 24, NOW))
    assert out.startswith("\x1b[H") and "\x1b[1;97;41m" in out and out.count("\x1b[0m") >= 1


# ---------------------------------------------------------------- plain mode
def test_plain_line_format():
    line = lv.plain_line(snap(l_buzz=True))
    for s in ("ax=+0.010", "|a|=1.000", "gyro=3.7", "L=2.50m/+0.00", "buzz=L", "hat=ON", "tof=120", "fall=armed", "cam=-"):
        assert s in line, s
    assert "\n" not in line


def _udp_pair():
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.setblocking(False)
    return rx, ("127.0.0.1", rx.getsockname()[1])


def test_plain_mode_prints_one_line_per_update_and_events():
    rx, addr = _udp_pair()
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    out = io.StringIO()

    def send():
        for i in range(30):
            ev = [{"id": 1, "t": time.time(), "kind": "fall", "side": None, "text": "peak 4g"}] if i > 3 else []
            tx.sendto(json.dumps(snap(t=time.time(), seq=i, events=ev)).encode(), addr)
            time.sleep(0.02)

    th = threading.Thread(target=send)
    th.start()
    lv.run_plain(rx, out, hz=20, max_lines=6, stop=lambda: False)
    th.join()
    lines = out.getvalue().splitlines()
    assert len(lines) >= 6
    assert sum(1 for ln in lines if ln.startswith("# event") and "fall" in ln) == 1
    assert all("NO DATA" not in ln for ln in lines)
    assert any(ln[2] == ":" and "ax=" in ln for ln in lines)


def test_plain_mode_says_no_data():
    rx, _ = _udp_pair()
    out = io.StringIO()
    lv.run_plain(rx, out, hz=10, max_lines=1)
    assert out.getvalue().strip() == "NO DATA: is hat.main running?"


# ---------------------------------------------------------------- real terminal: starts, draws, restores on Ctrl+C
PI_BRINGUP = str(Path(__file__).resolve().parents[2])


def _run_in_pty(sig=None, key=None, send=True):
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    env = {**os.environ, "PYTHONPATH": PI_BRINGUP}
    p = subprocess.Popen([sys.executable, "-m", "hat.tools.live_view", "--port", str(port)], stdin=slave, stdout=slave,
                         stderr=subprocess.PIPE, env=env, close_fds=True, start_new_session=True)
    os.close(slave)
    buf = b""
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def pump(dur):
        nonlocal buf
        end = time.time() + dur
        while time.time() < end:
            if send:
                tx.sendto(json.dumps(snap(t=time.time(), l_buzz=True)).encode(), ("127.0.0.1", port))
            r, _, _ = select.select([master], [], [], 0.05)
            if r:
                try:
                    buf += os.read(master, 65536)
                except OSError:
                    return

    pump(1.0)
    if sig:
        p.send_signal(sig)
    if key:
        os.write(master, key)
    deadline = time.time() + 5
    while p.poll() is None and time.time() < deadline:
        pump(0.1)
    rc = p.poll()
    if rc is None:
        p.kill()
    pump(0.2)
    os.close(master)
    return rc, buf, p.stderr.read().decode()


def _check_restored(rc, buf, err):
    assert rc == 0, (rc, err)
    assert b"\x1b[?1049h" in buf and b"ARGUS LIVE" in buf and b"HAT ON" in buf
    tail = buf[-200:]
    assert b"\x1b[?25h" in tail and b"\x1b[?1049l" in tail  # cursor back, main screen back


def test_ui_restores_terminal_on_ctrl_c():
    _check_restored(*_run_in_pty(sig=signal.SIGINT))


def test_ui_restores_terminal_on_q_and_sigterm():
    _check_restored(*_run_in_pty(key=b"q"))
    _check_restored(*_run_in_pty(sig=signal.SIGTERM))


def test_ui_with_no_data_shows_banner():
    rc, buf, err = _run_in_pty(key=b"q", send=False)
    assert rc == 0 and b"NO DATA: is hat.main running?" in buf


def test_viewer_never_imports_hardware_modules():
    code = ("import sys; import hat.tools.live_view; "
            "bad=[m for m in ('smbus2','gpiozero','lgpio','picamera2','hat.haptics','hat.minimu9','hat.tof','hat.camera','hat.ultrasonic')"
            " if m in sys.modules]; print(bad); sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-c", code], env={**os.environ, "PYTHONPATH": PI_BRINGUP}, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
