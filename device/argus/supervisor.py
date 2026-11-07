"""Module loading, polling, health checks, automatic fallback, hot-swap, shadow mode (spec §5.4).

* Impls live at ``hardware/<impl>/<module>/__init__.py`` and expose ``create(ctx)``.
* One poll thread per module drains ``read()`` into the :class:`SampleBus`. The thread outlives
  impl swaps, so ``reload`` replaces one module in milliseconds while the others keep running.
* Fallback triggers (spec §5.4 step 2): ``health().ok`` false, no samples for ``STALE_AFTER_MS``
  (imu/range 500 ms, hat_on 3 s, ...), a raising ``read()``, or a failed 1 s zero-intensity haptics
  self-test. The ``module_fallback`` record is passed to ``on_event`` (L3/L4 upload it).
* Staleness is measured on the injected clock, so tests can drive it with a virtual clock via
  :meth:`Supervisor.poll_once` / :meth:`Supervisor.check_once` without any thread.
"""

from __future__ import annotations

import importlib
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any

import numpy as np

from argus.bus import SampleBus
from argus.config import ArgusConfig, ConfigError, ModuleCfg, load_config
from argus.contracts import (
    MODULE_NAMES,
    PROTOCOL_FOR,
    STALE_AFTER_MS,
    Clock,
    ModuleContext,
    MonotonicClock,
)

log = logging.getLogger(__name__)

STREAM_FOR: dict[str, str] = {"imu": "imu", "range": "range", "hat_on": "tof", "env": "env"}
VAR_DIR = Path(__file__).resolve().parents[1] / "var"
# Hardware that can be opened by only one owner at a time (GPIO pins claimed through lgpio, the camera).
# Swapping these must release the old impl BEFORE the new one starts. I2C modules can be shared.
EXCLUSIVE_MODULES = frozenset({"range", "haptics", "camera"})
HEALTH_EVERY_S = 0.1
SELFTEST_EVERY_S = 1.0

DEFAULT_CONFIG = "<default>"  # config_path value meaning "device/config.yaml + config.local.yaml", re-read on reload

EventCallback = Callable[[dict[str, Any]], None]


class ModuleLoadError(RuntimeError):
    """An impl could not be imported, created, or does not satisfy its Protocol."""


def load_impl(module: str, impl: str, ctx: ModuleContext) -> Any:
    """Import ``hardware.<impl>.<module>`` and call ``create(ctx)``; verify the Protocol (spec §5.2)."""
    if module not in PROTOCOL_FOR:
        raise ModuleLoadError(f"unknown module {module!r}; known: {list(MODULE_NAMES)}")
    path = f"hardware.{impl}.{module}"
    try:
        mod = importlib.import_module(path)
    except ImportError as e:
        raise ModuleLoadError(f"cannot import {path}: {e}") from e
    factory = getattr(mod, "create", None)
    if factory is None:
        raise ModuleLoadError(f"{path} has no create(ctx) factory")
    try:
        inst = factory(ctx)
    except Exception as e:
        raise ModuleLoadError(f"{path}.create() failed: {type(e).__name__}: {e}") from e
    if not isinstance(inst, PROTOCOL_FOR[module]):
        raise ModuleLoadError(f"{path}.create() returned {type(inst).__name__}, which does not satisfy "
                              f"{PROTOCOL_FOR[module].__name__} (missing methods?)")
    return inst


@dataclass
class _Slot:
    name: str
    cfg: ModuleCfg
    impl_name: str = ""
    inst: Any = None
    on_fallback: bool = False
    gave_up: bool = False
    last_progress_ns: int = 0
    last_error: str = ""
    lock: threading.RLock = field(default_factory=threading.RLock)


class _Proxy:
    """Stable handle for a module whose impl may be swapped underneath (used by L3)."""

    def __init__(self, sup: Supervisor, slot: _Slot) -> None:
        self._sup, self._slot = sup, slot

    def __getattr__(self, item: str) -> Any:
        with self._slot.lock:  # wait out an in-progress swap of exclusive hardware
            return getattr(self._slot.inst, item)


class HapticsProxy(_Proxy):
    """``set``/``off_all`` always hit the live driver; a raising driver triggers fallback + one retry."""

    def __init__(self, sup: Supervisor, slot: _Slot) -> None:
        super().__init__(sup, slot)
        self.level = {"top": 0.0, "back": 0.0}

    @property
    def busy(self) -> bool:
        return any(v > 0 for v in self.level.values())

    def set(self, motor: str, intensity: float) -> None:
        if motor in self.level:
            self.level[motor] = float(intensity)
        self._call(lambda d: d.set(motor, intensity))

    def off_all(self) -> None:
        self.level = dict.fromkeys(self.level, 0.0)
        self._call(lambda d: d.off_all())

    def _call(self, fn: Callable[[Any], None]) -> None:
        for attempt in (0, 1):
            try:
                with self._slot.lock:
                    fn(self._slot.inst)
                return
            except Exception as e:  # noqa: BLE001
                log.error("haptics driver raised: %s", e)
                if attempt == 0:
                    self._sup.fallback("haptics", f"driver raised {type(e).__name__}: {e}")


class Supervisor:
    def __init__(
        self,
        cfg: ArgusConfig,
        bus: SampleBus,
        clock: Clock | None = None,
        *,
        on_event: EventCallback | None = None,
        scenario: str | None = None,
        config_path: Path | str | None = None,
        poll_interval_s: float = 0.005,
    ) -> None:
        self.cfg, self.bus = cfg, bus
        self.clock = clock or MonotonicClock()
        self.on_event = on_event or (lambda rec: None)
        self.scenario = scenario
        self.config_path = config_path
        self.poll_interval_s = poll_interval_s
        self._slots = {n: _Slot(n, cfg.modules[n]) for n in MODULE_NAMES}
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self.haptics = HapticsProxy(self, self._slots["haptics"])
        self.camera = _Proxy(self, self._slots["camera"])

    # ------------------------------------------------------------ lifecycle
    def start(self, run_threads: bool = True) -> None:
        """Load + start every module. ``run_threads=False`` leaves polling to the caller (tests drive
        ``poll_once``/``check_once`` on a virtual clock)."""
        self._stop.clear()
        for name, slot in self._slots.items():
            try:
                self._swap(slot, slot.cfg.impl)
            except Exception as e:  # noqa: BLE001
                log.error("module %s: impl %s failed to start: %s", name, slot.cfg.impl, e)
                self.fallback(name, f"start failed: {e}")
            if run_threads:
                t = threading.Thread(target=self._loop, args=(name,), name=f"argus-{name}", daemon=True)
                t.start()
                self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=2.0)
        self._threads.clear()
        for slot in self._slots.values():
            if slot.inst is not None:
                try:
                    slot.inst.stop()
                except Exception:
                    log.exception("stopping %s", slot.name)

    def module(self, name: str) -> Any:
        """The live impl instance (changes after a swap; use ``haptics``/``camera`` for stable handles)."""
        return self._slots[name].inst

    def status(self) -> dict[str, dict[str, Any]]:
        out = {}
        for n, s in self._slots.items():
            h = None
            try:
                h = asdict(s.inst.health()) if s.inst is not None else None
            except Exception as e:  # noqa: BLE001
                h = {"ok": False, "note": f"health() raised {e}"}
            out[n] = {"impl": s.impl_name or None, "fallback_cfg": s.cfg.fallback, "on_fallback": s.on_fallback,
                      "gave_up": s.gave_up, "health": h, "last_error": s.last_error}
        return out

    # ------------------------------------------------------------ polling + health
    def poll_once(self, name: str) -> int:
        """Drain one module's ``read()`` into the bus. Returns the number of samples published."""
        slot = self._slots[name]
        stream = STREAM_FOR.get(name)
        with slot.lock:  # not while an exclusive module is being swapped
            inst = slot.inst
            if stream is None or inst is None:
                return 0
            try:
                samples = inst.read()
            except Exception as e:  # noqa: BLE001 - contract violation: read() must not raise
                self.fallback(name, f"read() raised {type(e).__name__}: {e}")
                return 0
            if samples:
                self.bus.publish(stream, samples)
                slot.last_progress_ns = self.clock.now_ns()
            return len(samples)

    def check_once(self, name: str, *, selftest: bool = True) -> None:
        """Health / staleness / haptics self-test for one module; engages fallback when needed."""
        slot = self._slots[name]
        with slot.lock:  # not while an exclusive module is being swapped (the old impl is stopped then)
            self._check_locked(name, slot, selftest)

    def _check_locked(self, name: str, slot: _Slot, selftest: bool) -> None:
        inst = slot.inst
        if inst is None or slot.gave_up:
            return
        try:
            h = inst.health()
        except Exception as e:  # noqa: BLE001
            self.fallback(name, f"health() raised {type(e).__name__}: {e}")
            return
        if not h.ok:
            self.fallback(name, f"health not ok: {h.note or 'no detail'}")
            return
        limit = STALE_AFTER_MS[name]  # type: ignore[index]
        if limit is not None:
            if name in STREAM_FOR:
                age_ms = (self.clock.now_ns() - slot.last_progress_ns) / 1e6
            else:
                age_ms = h.last_sample_age_ms or 0.0
            if age_ms > limit:
                self.fallback(name, f"stale: no samples for {age_ms:.0f} ms (limit {limit:.0f} ms)")
                return
        if name == "haptics" and selftest and not self.haptics.busy:
            try:  # zero-intensity self-test (spec §5.4); skipped while a pattern is playing
                inst.set("top", 0.0)
                inst.set("back", 0.0)
            except Exception as e:  # noqa: BLE001
                self.fallback(name, f"haptics self-test failed: {type(e).__name__}: {e}")

    def _loop(self, name: str) -> None:
        last_check = last_test = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            self.poll_once(name)
            if now - last_check >= HEALTH_EVERY_S:
                last_check = now
                run_test = now - last_test >= SELFTEST_EVERY_S
                if run_test:
                    last_test = now
                self.check_once(name, selftest=run_test)
            self._stop.wait(self.poll_interval_s)

    # ------------------------------------------------------------ swap / fallback / reload
    def _ctx(self, slot: _Slot) -> ModuleContext:
        return ModuleContext(self.clock, dict(slot.cfg.options), self.scenario)

    def _start_checked(self, slot: _Slot, inst: Any) -> str | None:
        """Start ``inst`` and check ``health()``. Drivers never raise from ``start()``; an init failure (busy
        GPIO, camera in use) only shows up as ``health().ok == False``. Returns the problem, or None."""
        try:
            inst.start()
            h = inst.health()
        except Exception as e:  # noqa: BLE001
            return f"{type(e).__name__}: {e}"
        return None if h.ok else (h.note or "health not ok right after start()")

    def _swap(self, slot: _Slot, impl: str) -> None:
        """Replace the live impl with ``impl``.

        Streaming I2C modules: create+start the new one, then switch and stop the old (the old keeps
        running if the new one fails to load). Exclusive hardware (``EXCLUSIVE_MODULES``): the old impl
        must release the pins/camera first, so it is stopped, the new one is started and its health
        checked, and on failure the old impl is started again and ``ModuleLoadError`` says so.
        """
        new = load_impl(slot.name, impl, self._ctx(slot))
        if slot.name in EXCLUSIVE_MODULES:
            self._swap_exclusive(slot, new, impl)
            return
        new.start()
        with slot.lock:
            old, slot.inst, slot.impl_name = slot.inst, new, impl
            slot.last_progress_ns = self.clock.now_ns()
        if old is not None:
            try:
                old.stop()
            except Exception:
                log.exception("stopping old %s impl", slot.name)

    def _swap_exclusive(self, slot: _Slot, new: Any, impl: str) -> None:
        with slot.lock:
            old, old_impl = slot.inst, slot.impl_name
            if old is not None:
                try:
                    old.stop()  # release the GPIO pins / camera before the new impl claims them
                except Exception:
                    log.exception("stopping old %s impl", slot.name)
            problem = self._start_checked(slot, new)
            if problem is None:
                slot.inst, slot.impl_name = new, impl
                slot.last_progress_ns = self.clock.now_ns()
                return
            try:
                new.stop()  # give back whatever it did manage to claim
            except Exception:
                log.exception("stopping failed %s impl %s", slot.name, impl)
            if old is None:
                raise ModuleLoadError(f"{slot.name}: {impl} did not start: {problem}")
            restored = "could NOT be restarted"
            try:
                again = load_impl(slot.name, old_impl, self._ctx(slot))
                err = self._start_checked(slot, again)
                if err is None:
                    slot.inst, slot.last_progress_ns = again, self.clock.now_ns()
                    restored = "was restarted and is running again"
                else:
                    again.stop()
                    restored = f"could NOT be restarted ({err})"
            except Exception as e:  # noqa: BLE001
                restored = f"could NOT be restarted ({type(e).__name__}: {e})"
            raise ModuleLoadError(f"{slot.name}: {impl} did not start: {problem}; "
                                  f"the previous impl {old_impl} {restored}")

    def fallback(self, name: str, reason: str) -> bool:
        """Switch ``name`` to its configured fallback impl. Emits a ``module_fallback`` record."""
        slot = self._slots[name]
        with slot.lock:
            if slot.gave_up:
                return False
            failed = slot.impl_name or slot.cfg.impl
            target = slot.cfg.fallback
            slot.last_error = reason
            ok = False
            if target is not None and not slot.on_fallback:
                try:
                    self._swap(slot, target)
                    slot.on_fallback = ok = True
                except Exception as e:  # noqa: BLE001
                    reason = f"{reason}; fallback {target} also failed: {e}"
                    log.error("module %s: %s", name, reason)
            if not ok:
                slot.gave_up = True
            log.warning("module %s: %s -> %s (%s)", name, failed, target if ok else None, reason)
            self.on_event({
                "type": "module_fallback", "module": name, "from_impl": failed,
                "to_impl": target if ok else None, "reason": reason, "t_ns": self.clock.now_ns(),
            })
            return ok

    def reload(self, name: str, impl: str | None = None) -> float:
        """Planned hot-swap (spec §5.4 step 1): re-read config (unless ``impl`` given), swap one module.

        Returns elapsed seconds. On failure the old impl keeps running and the error propagates.
        """
        slot = self._slots[name]
        t0 = time.perf_counter()
        old_cfg = cfg = slot.cfg
        if self.config_path is not None:  # re-read the file(s): new options must reach the new impl (spec §5.5)
            try:
                cfg = load_config(None if self.config_path == DEFAULT_CONFIG else self.config_path).modules[name]
            except ConfigError:
                if impl is None:
                    raise
        target = impl or cfg.impl
        slot.cfg = ModuleCfg(impl=target, fallback=cfg.fallback, options=cfg.options)
        try:
            self._swap(slot, target)
        except Exception:
            slot.cfg = old_cfg
            raise
        slot.on_fallback = slot.gave_up = False
        slot.last_error = ""
        dt = time.perf_counter() - t0
        log.info("reloaded %s -> %s in %.0f ms", name, target, dt * 1e3)
        return dt


# ---------------------------------------------------------------- shadow mode (spec §5.4 step 3)
def _record(side: str, impl: str, sample: Any) -> dict[str, Any]:
    d = asdict(sample) if is_dataclass(sample) else dict(vars(sample))
    return {"side": side, "impl": impl, **d}


def _diff_summary(a: list[Any], b: list[Any]) -> dict[str, Any]:
    """Nearest-in-time comparison of numeric fields (per sensor_id for the range array)."""
    if not a or not b:
        return {}
    fields = [f for f, v in asdict(a[0]).items() if f not in ("t_ns",) and isinstance(v, (int, float))]
    out: dict[str, dict[str, float]] = {}
    tb = np.array([s.t_ns for s in b])
    for f in fields:
        diffs = []
        for s in a:
            j = int(np.argmin(np.abs(tb - s.t_ns)))
            o = b[j]
            if getattr(o, "sensor_id", None) != getattr(s, "sensor_id", None):
                continue
            va, vb = getattr(s, f), getattr(o, f)
            if va is not None and vb is not None:
                diffs.append(abs(va - vb))
        if diffs:
            out[f] = {"mean_abs_diff": float(np.mean(diffs)), "max_abs_diff": float(np.max(diffs)),
                      "matched": len(diffs)}
    return out


def shadow(
    module: str,
    impl_a: str,
    impl_b: str,
    seconds: float,
    *,
    clock: Clock | None = None,
    options: dict[str, Any] | None = None,
    scenario: str | None = None,
    out_dir: Path | str | None = None,
    alternate: bool = False,
    step_s: float = 0.01,
) -> Path:
    """Run two impls of a streaming module side by side and write a JSONL diff log.

    ``alternate=True`` runs them in alternating 1 s windows for buses that forbid sharing the sensor
    (no sample-level diff then, only rate/count). With a virtual clock (has ``advance``) time is
    stepped instantly; otherwise it runs in real time. Returns the log path.
    """
    if module not in STREAM_FOR:
        raise ValueError(f"shadow mode needs a streaming module {list(STREAM_FOR)}; {module!r} is "
                         "exclusive-access, use A/B bench runs instead (spec §5.4)")
    clock = clock or MonotonicClock()
    ctx = ModuleContext(clock, dict(options or {}), scenario)
    insts = {"a": load_impl(module, impl_a, ctx), "b": load_impl(module, impl_b, ctx)}
    names = {"a": impl_a, "b": impl_b}
    out = Path(out_dir or VAR_DIR / "shadow")
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{module}-{impl_a}-vs-{impl_b}-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    got: dict[str, list[Any]] = {"a": [], "b": []}
    running: set[str] = set()

    def activate(keys: set[str]) -> None:
        for k in set(running) - keys:
            insts[k].stop()
        for k in keys - running:
            insts[k].start()
        running.clear()
        running.update(keys)

    activate(set() if alternate else {"a", "b"})
    steps = round(seconds / step_s)
    virtual = hasattr(clock, "advance")
    for i in range(steps):
        if alternate:
            activate({"a"} if int(i * step_s) % 2 == 0 else {"b"})
        if virtual:
            clock.advance(step_s)  # type: ignore[attr-defined]
        else:
            time.sleep(step_s)
        for k in running:
            got[k].extend(insts[k].read())
    activate(set())
    with path.open("w") as f:
        for k in ("a", "b"):
            for s in got[k]:
                f.write(json.dumps(_record(k, names[k], s)) + "\n")
        summary = {
            "summary": {"module": module, "seconds": seconds, "alternate": alternate,
                        "count": {f"{k}:{names[k]}": len(got[k]) for k in got},
                        "rate_hz": {f"{k}:{names[k]}": len(got[k]) / seconds for k in got},
                        "diff": {} if alternate else _diff_summary(got["a"], got["b"])}
        }
        f.write(json.dumps(summary) + "\n")
    return path
