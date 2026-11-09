"""Live terminal view of the running hat.  Listen-only: never opens I2C, GPIO or the camera.

  python -m hat.tools.live_view            # full-screen ANSI UI (80x24 is enough), q or Ctrl+C quits
  python -m hat.tools.live_view --plain    # one text line per update, for pipes / logs

hat.main publishes a ~20 Hz UDP snapshot to 127.0.0.1:47321 (hat/telemetry.py, config LIVE_TELEMETRY).
Red = a value is past a real threshold taken from the RUNNING hat.main (sent in each snapshot).
"""

from __future__ import annotations

import argparse
import json
import os
import select
import signal
import socket
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from hat import config as cfg

Seg = Tuple[str, str]  # (text, style)
Line = List[Seg]

STALE_S = 1.5
LOG_KEEP = 10
ANSI = {
    "": "",
    "dim": "\x1b[2m",
    "bold": "\x1b[1m",
    "red": "\x1b[1;31m",
    "green": "\x1b[32m",
    "yellow": "\x1b[33m",
    "cyan": "\x1b[36m",
    "flash": "\x1b[1;97;41m",  # white on red: motor buzzing
    "alert": "\x1b[1;97;41m",
    "ok": "\x1b[1;30;42m",
}
RESET = "\x1b[0m"


def default_thr() -> Dict[str, float]:
    return {
        "impact_g": cfg.IMPACT_G,
        "freefall_g": cfg.FREEFALL_G,
        "gyro_gate_dps": cfg.US_GYRO_GATE_DPS,
        "warn_m": cfg.US_WARN_DISTANCE_M,
        "min_closing_mps": cfg.US_MIN_OBJ_MPS,
        "max_range_m": cfg.US_MAX_RANGE_M,
        "buzz_on_s": cfg.HAPTIC_WARN_ON_S,
    }


# ----------------------------------------------------------------------------- model
class ViewState:
    """Latest snapshot + the event log, with de-duplication across datagrams."""

    def __init__(self) -> None:
        self.snap: Optional[Dict[str, Any]] = None
        self.last_rx = 0.0
        self.events: List[Dict[str, Any]] = []
        self._seen: set = set()
        self.boot: Optional[str] = None
        self.count = 0

    def feed(self, data: bytes, now: float) -> bool:
        try:
            d = json.loads(data)
            if not isinstance(d, dict):
                return False
        except ValueError:
            return False
        self.snap, self.last_rx = d, now
        self.count += 1
        boot = d.get("boot")
        if boot != self.boot:
            self.boot = boot
            self._seen.clear()
        for e in d.get("events") or []:
            try:
                key = (boot, e["id"])
            except (KeyError, TypeError):
                continue
            if key not in self._seen:
                self._seen.add(key)
                self.events.append(e)
        del self.events[:-LOG_KEEP]
        return True

    def age(self, now: float) -> Optional[float]:
        return None if self.snap is None else now - self.last_rx

    def stale(self, now: float) -> bool:
        a = self.age(now)
        return a is None or a > STALE_S


# ----------------------------------------------------------------------------- rendering helpers
def _fmt(v: Any, spec: str, width: int, none: str = "--") -> str:
    return (none if v is None else format(v, spec)).rjust(width)


def _pad(line: Line, width: int) -> Line:
    """Pad / truncate to exactly `width` visible columns."""
    out: Line = []
    used = 0
    for text, style in line:
        if used >= width:
            break
        text = text[: width - used]
        out.append((text, style))
        used += len(text)
    if used < width:
        out.append((" " * (width - used), ""))
    return out


def _put(canvas: List[List[Seg]], row: int, col: int, text: str, style: str = "") -> None:
    """Write text into a canvas of single cells."""
    if not 0 <= row < len(canvas):
        return
    for i, ch in enumerate(text):
        c = col + i
        if 0 <= c < len(canvas[row]) and ch != "\0":
            canvas[row][c] = (ch, style)


def _canvas(rows: int, cols: int) -> List[List[Seg]]:
    return [[(" ", "") for _ in range(cols)] for _ in range(rows)]


def _compress(cells: List[Seg]) -> Line:
    out: Line = []
    for ch, st in cells:
        if out and out[-1][1] == st:
            out[-1] = (out[-1][0] + ch, st)
        else:
            out.append((ch, st))
    return out


def _ago(t: float, now_wall: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(t))


# ----------------------------------------------------------------------------- the helmet
LEFT_W = 38


def draw_helmet(snap: Dict[str, Any], thr: Dict[str, float], rows: int, tick: int) -> List[Line]:
    """Top-down helmet, front at the top. Beams extend downward (behind the wearer)."""
    c = _canvas(rows, LEFT_W)
    mid = LEFT_W // 2
    lx, rx = mid - 6, mid + 6  # beam columns
    on = snap.get("hat_on")
    outline = "green" if on else ("yellow" if on is False else "dim")

    _put(c, 0, mid - 5, "FRONT  ^", "bold")
    shell = [
        "   .-----------.   ",
        "  /             \\  ",
        " |               | ",
        " |               | ",
        "  '-------------'  ",
    ]
    top = 1
    x0 = mid - 9
    for i, ln in enumerate(shell):
        _put(c, top + i, x0, ln, outline)
    # hat-on indicator + raw ToF reading in the middle
    label, style = {True: ("[ HAT ON ]", "ok"), False: ("[ HAT OFF ]", "yellow"), None: ("[ HAT ? ]", "dim")}[on]
    _put(c, top + 2, mid - len(label) // 2, label, style)
    mm = snap.get("tof_mm")
    _put(c, top + 3, mid - 3, (f"{mm}mm" if mm is not None else "--").center(6), "dim")
    # DRV motors inside the shell, left / right: flash while buzzing
    for side, col in (("l", x0 + 2), ("r", x0 + 14)):
        tag = side.upper()
        if snap.get(f"{side}_buzz"):
            _put(c, top + 3, col, f"#{tag}#" if tick % 2 == 0 else f"*{tag}*", "flash")
        else:
            _put(c, top + 3, col, f"({tag})", "dim")
    # beams behind
    beam_top = top + len(shell)
    beam_rows = rows - beam_top - 1
    if beam_rows >= 1:
        warn = thr.get("warn_m", 1.8) or 1.8
        rng = thr.get("max_range_m", 3.0) or 3.0
        for side, col, name in (("l", lx, "REAR-L"), ("r", rx, "REAR-R")):
            d = snap.get(f"{side}_dist")
            raw = snap.get(f"{side}_raw")
            shown = d if d is not None else raw
            hot = shown is not None and shown < warn
            n_rows = beam_rows
            fill = 0 if shown is None else max(1, min(n_rows, int(round(min(shown, rng) / rng * n_rows))))
            for i in range(n_rows):
                if i < fill:
                    ch, st = ("|", "red" if hot else "cyan")
                    if i == fill - 1 and shown is not None:
                        ch = "v"
                else:
                    ch, st = (":", "dim")
                _put(c, beam_top + i, col, ch, st)
            # warn-distance tick
            wi = min(n_rows - 1, int(round(warn / rng * n_rows)) - 1)
            if 0 <= wi < n_rows and wi >= fill:
                _put(c, beam_top + wi, col, "-", "yellow")
            txt = "--" if shown is None else f"{shown:.2f}m"
            _put(c, beam_top + min(fill, n_rows - 1) if shown is not None else beam_top, col + 2,
                 txt, "red" if hot else ("" if shown is not None else "dim"))
            _put(c, rows - 1, col - 2, name, "bold")
    return [_compress(r) for r in c]


# ----------------------------------------------------------------------------- numbers
def draw_numbers(snap: Dict[str, Any], thr: Dict[str, float]) -> List[Line]:
    L: List[Line] = []
    mag = snap.get("mag")
    mag_hi = mag is not None and mag >= thr["impact_g"]
    mag_lo = mag is not None and mag < thr["freefall_g"]
    L.append([("IMU ", "bold"), (f"ax{_fmt(snap.get('ax'), '+.3f', 7)} ay{_fmt(snap.get('ay'), '+.3f', 7)} "
                                 f"az{_fmt(snap.get('az'), '+.3f', 7)}", "")])
    st = "alert" if mag_hi else ("red" if mag_lo else "")
    note = " IMPACT" if mag_hi else (" FREEFALL" if mag_lo else "")
    L.append([("    |a| ", ""), (f"{_fmt(mag, '.3f', 6)} g", st), (note, st),
              (f"   (>{thr['impact_g']:g} imp, <{thr['freefall_g']:g} ff)", "dim")])
    gx, gy, gz = snap.get("gx"), snap.get("gy"), snap.get("gz")
    if gx is None or gy is None or gz is None:
        L.append([("GYRO ", "bold"), ("not calibrated yet (hold still ~3 s)", "yellow")])
    else:
        g = (gx * gx + gy * gy + gz * gz) ** 0.5
        hot = g > thr["gyro_gate_dps"]
        L.append([("GYRO ", "bold"), (f"{gx:+7.1f} {gy:+7.1f} {gz:+7.1f} dps", "")])
        L.append([("     |g| ", ""), (f"{g:6.1f} dps", "alert" if hot else ""),
                  (" GATED" if hot else "", "alert"), (f"   (gate >{thr['gyro_gate_dps']:g})", "dim")])
    L.append([("-" * 39, "dim")])
    for side, name in (("l", "LEFT "), ("r", "RIGHT")):
        d, cl = snap.get(f"{side}_dist"), snap.get(f"{side}_close")
        near = d is not None and d < thr["warn_m"]
        fast = cl is not None and cl >= thr["min_closing_mps"]
        L.append([(name, "bold"), (" d ", ""), (f"{_fmt(d, '.2f', 5)} m", "red" if near else ""),
                  ("  close ", ""), (f"{_fmt(cl, '+.2f', 6)} m/s", "red" if fast and near else ("yellow" if fast else ""))])
    flags = []
    flags.append(("GATED", "yellow") if snap.get("gated") else ("gate -", "dim"))
    flags.append(("  ", ""))
    flags.append(("MOVING", "yellow") if snap.get("moving") else ("moving -", "dim"))
    L.append([("     ", "")] + flags + [(f"   (warn <{thr['warn_m']:g} m)", "dim")])
    L.append([("-" * 39, "dim")])
    lb, rb = bool(snap.get("l_buzz")), bool(snap.get("r_buzz"))
    L.append([("DRV  ", "bold"), ("L BUZZ", "flash") if lb else ("L idle", "dim"), ("   ", ""),
              ("R BUZZ", "flash") if rb else ("R idle", "dim")])
    f = snap.get("fall")
    if f is None:
        L.append([("FALL ", "bold"), ("--", "dim")])
    else:
        bits: Line = [("FALL ", "bold")]
        bits.append(("armed", "green") if f.get("armed") else ("DISARMED", "yellow"))
        bits.append((" ", ""))
        bits.append(("CANDIDATE", "alert") if f.get("candidate") else ("cand -", "dim"))
        bits.append((" ", ""))
        bits.append(("WAIT-STILL", "red") if f.get("waiting_still") else ("still -", "dim"))
        bits.append((" ", ""))
        bits.append(("COOLDOWN", "yellow") if f.get("cooldown") else ("cool -", "dim"))
        L.append(bits)
    L.append([("SYS  ", "bold"),
              (f"cfg-loss {snap.get('config_losses', 0)}", "red" if snap.get("config_losses") else ""),
              (f"  i2c-err {snap.get('i2c_errors', 0)}", "red" if snap.get("i2c_errors") else ""),
              ("  cam ", ""), ("REC", "red") if snap.get("camera") else ("idle", "dim")])
    return L


# ----------------------------------------------------------------------------- full frame
def render(state: ViewState, width: int = 80, height: int = 24, now: Optional[float] = None,
           tick: int = 0, port: int = 0) -> List[Line]:
    """One screen as `height` lines of exactly `width` columns each (list of (text, style) segments)."""
    now = time.time() if now is None else now
    snap = state.snap
    stale = state.stale(now)
    body_rows = max(12, height - 2 - LOG_KEEP) if height >= 24 else max(6, height - 6)
    log_rows = height - 1 - body_rows - 1  # title + body + events header + log
    lines: List[Line] = []

    # title row
    if snap is None or stale:
        age = state.age(now)
        right = "no data yet" if age is None else f"last data {age:.0f}s ago"
        title: Line = [("ARGUS LIVE ", "bold"), (f"- {right}", "red"), ("   q/Ctrl+C quit", "dim")]
    else:
        up = snap.get("up", 0)
        title = [("ARGUS LIVE ", "bold"), (time.strftime("%H:%M:%S", time.localtime(snap.get("t", now))), ""),
                 (f"  up {up:.0f}s  #{snap.get('seq', 0)}", "dim"), ("   q/Ctrl+C quit", "dim")]
        rec = snap.get("rec")
        if rec and rec.get("dropped"):
            title.append((f"  REC-DROPPED {rec['dropped']}", "red"))
    lines.append(title)

    if snap is None or stale:
        msg = ["", "", "NO DATA: is hat.main running?", "",
               f"listening on UDP 127.0.0.1:{port or cfg.LIVE_UDP_PORT}  (hat/config.py LIVE_TELEMETRY must be True)", ""]
        body = [[("", "")] for _ in range(body_rows)]
        for i, m in enumerate(msg[:body_rows]):
            pad = max(0, (width - len(m)) // 2)
            body[i] = [(" " * pad + m, "alert" if "NO DATA" in m else "dim")]
        lines.extend(body)
    else:
        thr = {**default_thr(), **(snap.get("thr") or {})}
        left = draw_helmet(snap, thr, body_rows, tick)
        right = draw_numbers(snap, thr)
        right += [[("", "")]] * max(0, body_rows - len(right))
        for i in range(body_rows):
            lines.append(_pad(left[i], LEFT_W) + [(" ", "")] + right[i])

    lines.append([("-- events " + "-" * 70, "dim")])
    evs = state.events[-log_rows:] if log_rows > 0 else []
    for e in evs:
        kind = str(e.get("kind", ""))
        side = f" {e['side']}" if e.get("side") else ""
        style = {"fall": "alert", "near_miss": "red", "buzz": "red", "config_lost": "red",
                 "fall_suppressed": "yellow", "hat_off": "yellow", "hat_on": "green"}.get(kind, "")
        lines.append([(time.strftime("%H:%M:%S", time.localtime(e.get("t", 0))) + " ", "dim"),
                      (f"{kind.upper():<15}", style), (f"{side:<6} ", style), (str(e.get("text", "")), "")])
    while len(lines) < height:
        lines.append([("", "")])
    return [_pad(ln, width) for ln in lines[:height]]


def to_text(lines: Sequence[Line]) -> List[str]:
    return ["".join(t for t, _ in ln) for ln in lines]


def to_ansi(lines: Sequence[Line]) -> str:
    out = []
    for i, ln in enumerate(lines):
        s = "".join((ANSI.get(st, "") + t + RESET) if st else t for t, st in ln)
        out.append(s)
    return "\x1b[H" + "\r\n".join(out)


# ----------------------------------------------------------------------------- plain mode
def plain_line(snap: Dict[str, Any]) -> str:
    def v(k: str, spec: str) -> str:
        x = snap.get(k)
        return "--" if x is None else format(x, spec)

    gyro = "--"
    if snap.get("gx") is not None:
        gyro = f"{(snap['gx'] ** 2 + snap['gy'] ** 2 + snap['gz'] ** 2) ** 0.5:.1f}"
    f = snap.get("fall") or {}
    fs = ",".join(k for k in ("armed", "candidate", "waiting_still", "cooldown") if f.get(k)) or "-"
    buzz = ("L" if snap.get("l_buzz") else "") + ("R" if snap.get("r_buzz") else "") or "-"
    on = snap.get("hat_on")
    return (f"{time.strftime('%H:%M:%S', time.localtime(snap.get('t', 0)))}.{int((snap.get('t', 0) % 1) * 1000):03d} "
            f"ax={v('ax', '+.3f')} ay={v('ay', '+.3f')} az={v('az', '+.3f')} |a|={v('mag', '.3f')} gyro={gyro} "
            f"L={v('l_dist', '.2f')}m/{v('l_close', '+.2f')} R={v('r_dist', '.2f')}m/{v('r_close', '+.2f')} "
            f"gated={int(bool(snap.get('gated')))} moving={int(bool(snap.get('moving')))} buzz={buzz} "
            f"hat={'ON' if on else ('OFF' if on is False else '?')} tof={v('tof_mm', 'd')} fall={fs} "
            f"cfgloss={snap.get('config_losses', 0)} i2cerr={snap.get('i2c_errors', 0)} "
            f"cam={'REC' if snap.get('camera') else '-'}")


def event_line(e: Dict[str, Any]) -> str:
    return (f"# event {time.strftime('%H:%M:%S', time.localtime(e.get('t', 0)))} {e.get('kind')} "
            f"{e.get('side') or ''} {e.get('text', '')}").rstrip()


def run_plain(sock: socket.socket, out=sys.stdout, hz: float = 10.0, stop=lambda: False,
              max_lines: Optional[int] = None, clock=time.time) -> int:
    state = ViewState()
    period = 1.0 / hz
    last_print = 0.0
    last_nodata = 0.0
    shown_ev: set = set()
    n = 0
    while not stop() and (max_lines is None or n < max_lines):
        r, _, _ = select.select([sock], [], [], 0.05)
        now = clock()
        got = False
        while r:
            try:
                data, _ = sock.recvfrom(65535)
            except (BlockingIOError, InterruptedError):
                break
            got |= state.feed(data, now)
            r, _, _ = select.select([sock], [], [], 0)
        if state.snap is not None and not state.stale(now):
            if got and now - last_print >= period:
                last_print = now
                for e in state.events:
                    k = (state.boot, e.get("id"))
                    if k not in shown_ev:
                        shown_ev.add(k)
                        out.write(event_line(e) + "\n")
                        n += 1
                out.write(plain_line(state.snap) + "\n")
                out.flush()
                n += 1
        elif now - last_nodata >= 1.0:
            last_nodata = now
            out.write("NO DATA: is hat.main running?\n")
            out.flush()
            n += 1
    return 0


# ----------------------------------------------------------------------------- full-screen mode
def run_ui(sock: socket.socket, port: int, hz: float = 10.0) -> int:
    import termios
    import tty

    if not sys.stdout.isatty():
        print("stdout is not a terminal; use --plain", file=sys.stderr)
        return 2
    out = sys.stdout
    fd = sys.stdin.fileno() if sys.stdin.isatty() else None
    old = termios.tcgetattr(fd) if fd is not None else None
    state = ViewState()
    quit_flag = {"q": False}

    def _term(*_: Any) -> None:
        quit_flag["q"] = True

    prev = {s: signal.signal(s, _term) for s in (signal.SIGTERM, signal.SIGHUP)}
    try:
        out.write("\x1b[?1049h\x1b[?25l\x1b[2J")  # alternate screen, hide cursor
        out.flush()
        if fd is not None:
            tty.setcbreak(fd)
        tick = 0
        nxt = time.monotonic()
        while not quit_flag["q"]:
            wait_fds = [sock] + ([sys.stdin] if fd is not None else [])
            timeout = max(0.0, nxt - time.monotonic())
            r, _, _ = select.select(wait_fds, [], [], timeout)
            now = time.time()
            if sock in r:
                while True:
                    try:
                        data, _ = sock.recvfrom(65535)
                    except (BlockingIOError, InterruptedError):
                        break
                    state.feed(data, now)
            if fd is not None and sys.stdin in r:
                ch = os.read(fd, 16)
                if b"q" in ch.lower() or b"\x03" in ch:
                    break
            if time.monotonic() >= nxt:
                nxt += 1.0 / hz
                if nxt < time.monotonic():
                    nxt = time.monotonic() + 1.0 / hz
                tick += 1
                w, h = os.get_terminal_size(out.fileno())
                w, h = max(20, w), max(10, h)
                frame = render(state, width=min(w, 80), height=min(h, 24), now=now, tick=tick, port=port)
                out.write(to_ansi(frame))
                out.write("\x1b[J")
                out.flush()
    except KeyboardInterrupt:
        pass
    finally:
        out.write(RESET + "\x1b[?25h\x1b[?1049l")  # show cursor, leave alternate screen
        out.flush()
        if old is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        for s, h in prev.items():
            signal.signal(s, h)
    return 0


def open_socket(host: str, port: int) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.setblocking(False)
    return s


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="Live terminal view of the running hat (listen-only)")
    ap.add_argument("--plain", action="store_true", help="one line per update on stdout (for pipes)")
    ap.add_argument("--host", default=cfg.LIVE_UDP_HOST)
    ap.add_argument("--port", type=int, default=int(os.environ.get("ARGUS_LIVE_PORT", cfg.LIVE_UDP_PORT)))
    ap.add_argument("--hz", type=float, default=10.0, help="screen / line rate (default 10)")
    args = ap.parse_args(argv)
    try:
        sock = open_socket(args.host, args.port)
    except OSError as e:
        print(f"cannot listen on {args.host}:{args.port}: {e} (is another live_view running?)", file=sys.stderr)
        return 2
    try:
        if args.plain:
            try:
                return run_plain(sock, hz=args.hz)
            except (KeyboardInterrupt, BrokenPipeError):
                return 0
        return run_ui(sock, args.port, hz=args.hz)
    finally:
        sock.close()


if __name__ == "__main__":
    raise SystemExit(main())
