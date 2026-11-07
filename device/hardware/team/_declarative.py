"""Config-driven team adapters (spec §5.5): run a teammate's code, parse what it gives, emit contract samples.

``create("imu", ctx, adapter_dir)`` is all a module's ``adapter.py`` needs. It reads ``mapping.yaml`` beside
the adapter plus ``modules.<module>.options`` (the latter wins), see ``_mapping.py`` for the keys.

Streaming modules (imu, range, hat_on, env) pick one source:

* ``command:`` run their blocking script, parse each stdout line (``format: json`` or ``text``), or
* ``python: vendor.their_script`` (+ ``call: read_once``) import it and call it in a loop.

The adapter stamps every sample with ``ctx.clock`` on arrival (their timestamps are ignored), counts and
skips malformed readings, and restarts a crashed script with backoff. While the script is down ``health()``
is not ok, so the supervisor falls back at once (spec §5.4). Without a ``command``/``python`` (or other
transport) ``create`` raises ``NotImplementedError`` and the supervisor falls back as before.
"""

from __future__ import annotations

import collections
import contextlib
import importlib
import inspect
import logging
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from argus.contracts import ClipInfo, Health, ModuleContext
from hardware.team._adapter_base import StreamAdapter
from hardware.team._mapping import Mapper, MappingError, load_mapping, unknown_keys

log = logging.getLogger(__name__)
STREAMING = ("imu", "range", "hat_on", "env")


def _not_pasted(module: str, why: str) -> NotImplementedError:
    return NotImplementedError(f"team impl for {module} not pasted yet - see README ({why})")


def _argv(command: Any, adapter_dir: Path) -> list[str]:
    argv = shlex.split(command) if isinstance(command, str) else [str(c) for c in command]
    if argv and argv[0] in ("python", "python3"):
        argv[0] = sys.executable  # the venv's interpreter, so their script sees the same packages
    return argv


def _kill_group(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            proc.terminate()
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.kill()


def _spawn(argv: list[str], adapter_dir: Path, *, stdin: Any = subprocess.DEVNULL, stdout: Any = subprocess.PIPE
           ) -> tuple[subprocess.Popen, collections.deque]:
    """Start their script in its own process group; stderr is drained into a short tail for health notes."""
    proc = subprocess.Popen(argv, cwd=adapter_dir, stdin=stdin, stdout=stdout, stderr=subprocess.PIPE, text=True,
                            bufsize=1, errors="replace", env={**os.environ, "PYTHONUNBUFFERED": "1"},
                            start_new_session=True)
    tail: collections.deque[str] = collections.deque(maxlen=3)

    def drain() -> None:
        assert proc.stderr is not None
        for ln in proc.stderr:
            tail.append(ln.strip())

    threading.Thread(target=drain, name="team-stderr", daemon=True).start()
    return proc, tail


def _import(name: str, module: str) -> Any:
    """``vendor.their_script`` is looked up under ``hardware.team.<module>`` first, then as a plain import."""
    try:
        return importlib.import_module(f"hardware.team.{module}.{name}")
    except ModuleNotFoundError as e:
        if e.name and not f"hardware.team.{module}.{name}".startswith(e.name):
            raise  # their script is there but one of ITS imports is missing: say so
        return importlib.import_module(name)


# ================================================================== streaming modules
class DeclarativeStream(StreamAdapter):
    """imu / range / hat_on / env driven by a mapping. See the module docstring."""

    def __init__(self, ctx: ModuleContext, module: str, mapper: Mapper, mapping: dict[str, Any], adapter_dir: Path):
        super().__init__(ctx)
        self.module, self.mapper, self.m, self.dir = module, mapper, mapping, adapter_dir
        r = mapping.get("restart") or {}
        self._b0, self._bmax = float(r.get("initial_s", 0.5)), float(r.get("max_s", 10.0))
        self._warmup = float(mapping.get("warmup_s", 0.0))
        self._interval = float(mapping.get("interval_s", 0.01))
        self._proc: subprocess.Popen | None = None
        self._down = False
        self._got = False

    # ---- lifecycle
    def start(self) -> None:
        self._down = False
        super().start()

    def stop(self) -> None:
        self._stop.set()
        if self._proc is not None:
            _kill_group(self._proc)  # unblocks the reader thread
        super().stop()

    def health(self) -> Health:
        h = super().health()
        if self._down:
            h.ok = False
        h.note = h.note or self._note
        return h

    # ---- the read loop: run the source, and when it ends or crashes, restart it with backoff
    def run(self, emit: Any) -> None:
        delay: float | None = None
        while not self.stopping:
            self._got = False
            try:
                reason = self._once(emit)
            except Exception as e:  # noqa: BLE001
                reason = f"{type(e).__name__}: {e}"
            if self.stopping:
                return
            delay = self._b0 if (self._got or delay is None) else min(delay * 2, self._bmax)
            self._errors += 1
            self._down = True
            self._note = f"{reason}; restarting in {delay:.1f} s"
            log.warning("team %s: %s", self.module, self._note)
            self._stop.wait(delay)

    def _once(self, emit: Any) -> str:
        return self._once_command(emit) if self.m.get("command") else self._once_python(emit)

    def _once_command(self, emit: Any) -> str:
        proc, tail = _spawn(_argv(self.m["command"], self.dir), self.dir)
        self._proc = proc
        t0, lines = time.monotonic(), 0
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if self.stopping:
                    return "stopped"
                lines += 1
                self._handle(line, self.now(), t0, emit)  # stamped on arrival with the shared clock
            code = proc.wait()
        finally:
            _kill_group(proc)
        why = f"script exited with code {code}" + (" right after start, with no output" if lines == 0 else "")
        return why + (f" (stderr: {tail[-1]})" if tail else "")

    def _once_python(self, emit: Any) -> str:
        spec = str(self.m["python"])
        modname, _, func = spec.partition(":")
        mod = _import(modname, self.module)
        if self.m.get("setup"):
            getattr(mod, self.m["setup"])()
        fn = getattr(mod, func or self.m.get("call") or "read")
        t0, fails = time.monotonic(), 0
        while not self.stopping:
            try:
                res = fn()
                for item in res if inspect.isgenerator(res) else ([] if res is None else [res]):
                    if self.stopping:
                        break
                    self._handle(item, self.now(), t0, emit)
                fails = 0
            except Exception as e:
                fails += 1
                self._errors += 1
                self._note = f"{type(e).__name__}: {e}"
                if fails >= 3:
                    raise
            self._stop.wait(self._interval)
        return "stopped"

    def _handle(self, payload: Any, t_ns: int, t0: float, emit: Any) -> None:
        warming = time.monotonic() - t0 < self._warmup
        try:
            samples = self.mapper.parse(payload, t_ns)
        except Exception as e:  # noqa: BLE001 - a bad reading is counted and skipped, never raised
            if not warming:
                self._errors += 1
                self._note = f"bad line: {e}"
            return
        if warming:
            return
        for s in samples:
            emit(s)
        if samples:
            self._got, self._down = True, False


# ================================================================== haptics
class DeclarativeHaptics:
    """``set(motor, intensity)`` through their function, a command's stdin, or a serial port. Never raises."""

    def __init__(self, ctx: ModuleContext, mapping: dict[str, Any], adapter_dir: Path) -> None:
        self.ctx, self.m, self.dir = ctx, mapping, adapter_dir
        self.motors = {str(k): v for k, v in (mapping.get("motors") or {}).items()}
        self.line = str(mapping.get("line", "H,{motor},{pwm}\n"))
        self.scale = float(mapping.get("scale", 1.0))
        self._send: Callable[[str, float], None] | None = None
        self._proc: subprocess.Popen | None = None
        self._tail: collections.deque[str] = collections.deque(maxlen=3)
        self._port: Any = None
        self._mod: Any = None
        self._errors = 0
        self._started = False
        self._broken = False
        self._note = ""
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._started:
            return
        try:
            if self.m.get("command"):
                self._proc, self._tail = _spawn(_argv(self.m["command"], self.dir), self.dir,
                                                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
                self._send = self._send_text(lambda t: self._proc.stdin.write(t))  # type: ignore[union-attr]
            elif self.m.get("port"):
                import serial  # pyserial: `uv pip install pyserial`, only needed for the Arduino fallback

                self._port = serial.Serial(self.m["port"], int(self.m.get("baud", 115200)), timeout=0.2)
                self._send = self._send_text(lambda t: self._port.write(t.encode()))
            else:
                self._mod = _import(str(self.m["python"]).partition(":")[0], "haptics")
                if self.m.get("setup"):
                    getattr(self._mod, self.m["setup"])()
                fn = getattr(self._mod, str(self.m["python"]).partition(":")[2] or self.m.get("call") or "set_duty")
                self._send = lambda motor, level: fn(self.motors.get(motor, motor), level * self.scale)
            self._started, self._broken, self._note = True, False, ""
        except Exception as e:  # noqa: BLE001 - drivers do not raise from start(); health() tells
            self._errors += 1
            self._note = f"{type(e).__name__}: {e}"
            log.error("team haptics start failed: %s", self._note)

    def _send_text(self, write: Callable[[str], Any]) -> Callable[[str, float], None]:
        def send(motor: str, level: float) -> None:
            text = self.line.format(motor=self.motors.get(motor, motor), level=round(level, 3),
                                    pwm=round(level * 255), pct=round(level * 100))
            write(text)
            if self._proc is not None:
                self._proc.stdin.flush()  # type: ignore[union-attr]
        return send

    def stop(self) -> None:
        if self._started:
            self.off_all()
        self._started = False
        if self._proc is not None:
            with contextlib.suppress(Exception):
                self._proc.stdin.close()  # type: ignore[union-attr]
            _kill_group(self._proc)
            self._proc = None
        if self._port is not None:
            with contextlib.suppress(Exception):
                self._port.close()
            self._port = None
        if self._mod is not None and self.m.get("teardown"):
            with contextlib.suppress(Exception):
                getattr(self._mod, self.m["teardown"])()

    def set(self, motor: str, intensity: float) -> None:
        if self._send is None or not self._started:
            return
        if motor not in ("top", "back"):
            self._errors += 1
            log.error("team haptics: unknown motor %r", motor)
            return
        level = min(1.0, max(0.0, float(intensity)))
        with self._lock:
            try:
                self._send(motor, level)
                self._broken = False
            except Exception as e:  # noqa: BLE001
                self._errors += 1
                self._broken = True
                self._note = f"set({motor}) failed: {type(e).__name__}: {e}"

    def off_all(self) -> None:
        for m in ("top", "back"):
            self.set(m, 0.0)

    def health(self) -> Health:
        if self._proc is not None and self._proc.poll() is not None:
            tail = f" (stderr: {self._tail[-1]})" if self._tail else ""
            return Health(False, None, self._errors, f"haptics script exited with code {self._proc.poll()}{tail}")
        return Health(self._started and not self._broken, None, self._errors, self._note)


# ================================================================== camera (kept minimal on purpose)
class DeclarativeCamera:
    """Their ``save_command`` saves the last N seconds to a file; optional ``command`` keeps their capture alive.

    Placeholders in ``save_command``: ``{path}`` output file, ``{seconds}`` clip length, ``{end_ago_s}`` how long
    ago the clip should end, ``{start_ns}`` / ``{end_ns}`` the shared-clock window.
    """

    def __init__(self, ctx: ModuleContext, mapping: dict[str, Any], adapter_dir: Path) -> None:
        self.ctx, self.m, self.dir = ctx, mapping, adapter_dir
        self._proc: subprocess.Popen | None = None
        self._started = False
        self._errors = 0
        self._note = ""

    def start(self) -> None:
        if self._started:
            return
        if self.m.get("command"):
            self._proc, _ = _spawn(_argv(self.m["command"], self.dir), self.dir, stdout=subprocess.DEVNULL)
        self._started = True

    def stop(self) -> None:
        if self._proc is not None:
            _kill_group(self._proc)
            self._proc = None
        self._started = False

    def save_clip(self, t_start_ns: int, t_end_ns: int, path: str) -> ClipInfo:
        seconds = (t_end_ns - t_start_ns) / 1e9
        values = {"path": path, "seconds": f"{seconds:.2f}", "start_ns": t_start_ns, "end_ns": t_end_ns,
                  "end_ago_s": f"{max(0.0, (self.ctx.clock.now_ns() - t_end_ns) / 1e9):.2f}"}
        argv = [a.format(**values) for a in _argv(self.m["save_command"], self.dir)]
        try:
            r = subprocess.run(argv, check=False, cwd=self.dir, capture_output=True, text=True,
                               timeout=float(self.m.get("save_timeout_s", 30)))
            size = Path(path).stat().st_size if Path(path).exists() else 0
            if r.returncode != 0 or size == 0:
                raise RuntimeError(f"save_command exited {r.returncode}, file size {size}: {r.stderr.strip()[-200:]}")
        except Exception as e:
            self._errors += 1
            self._note = f"save_clip failed: {e}"
            raise
        return ClipInfo(path, t_start_ns, t_end_ns, seconds, size)

    def health(self) -> Health:
        alive = self._started and (self._proc is None or self._proc.poll() is None)
        note = self._note or ("" if alive or not self._started else "capture command exited")
        return Health(alive, None, self._errors, note)


# ================================================================== factory
def create(module: str, ctx: ModuleContext, adapter_dir: Path | str) -> Any:
    """Build the team impl of ``module`` from ``mapping.yaml`` + options, or raise ``NotImplementedError``."""
    adir = Path(adapter_dir)
    m = load_mapping(module, adir, ctx.options)
    for w in unknown_keys(module, m):
        log.warning("team %s mapping: %s", module, w)
    if module in STREAMING:
        if not (m.get("command") or m.get("python")):
            raise _not_pasted(module, "set `command:` or `python:` in mapping.yaml, see README")
        return DeclarativeStream(ctx, module, Mapper(module, m), m, adir)
    if module == "haptics":
        if not any(m.get(k) for k in ("command", "python", "port")):
            raise _not_pasted(module, "set `python:`, `command:` or `port:` in mapping.yaml")
        return DeclarativeHaptics(ctx, m, adir)
    if module == "camera":
        if not m.get("save_command"):
            raise _not_pasted(module, "set `save_command:` in mapping.yaml")
        return DeclarativeCamera(ctx, m, adir)
    raise MappingError(f"unknown module {module!r}")
