"""Exclusive hardware (GPIO pins, camera) must be released by the old impl BEFORE the new one starts.

Drivers never raise from start(): a busy pin shows up only as health().ok == False. A fake pin that can
be owned by one driver at a time reproduces the review's S1 finding.
"""

from __future__ import annotations

import time

import pytest

import argus.supervisor as supmod
from argus.bus import SampleBus
from argus.contracts import Health
from argus.supervisor import ModuleLoadError, Supervisor
from hardware.mock.clock import VirtualClock
from tests.unit.test_supervisor import make_cfg


class Pin:
    """One physical resource: claim() fails while somebody else holds it."""

    def __init__(self):
        self.owner = None
        self.log = []


class FakeDriver:
    def __init__(self, name, pin, *, broken=False):
        self.name, self.pin, self.broken = name, pin, broken
        self.ok = False
        self.level = 0.0

    def start(self):
        if self.broken or self.pin.owner is not None:  # busy: do not raise, just be unhealthy
            self.ok = False
            self.pin.log.append(f"start-failed:{self.name}")
            return
        self.pin.owner, self.ok = self.name, True
        self.pin.log.append(f"start:{self.name}")

    def stop(self):
        if self.pin.owner == self.name:
            self.pin.owner = None
        self.ok = False
        self.pin.log.append(f"stop:{self.name}")

    def set(self, motor, intensity):
        self.level = intensity

    def off_all(self):
        self.level = 0.0

    def health(self):
        return Health(ok=self.ok, note="" if self.ok else "GPIO busy")


@pytest.fixture
def world(monkeypatch):
    pin = Pin()
    made: dict[str, list[FakeDriver]] = {}
    broken: set[str] = set()

    def fake_load(module, impl, ctx):
        if module != "haptics":
            return real_load(module, impl, ctx)
        d = FakeDriver(impl, pin, broken=impl in broken)
        made.setdefault(impl, []).append(d)
        return d

    real_load = supmod.load_impl
    monkeypatch.setattr(supmod, "load_impl", fake_load)
    events: list[dict] = []
    cfg = make_cfg(haptics={"impl": "argus", "fallback": "team"})
    s = Supervisor(cfg, SampleBus(), VirtualClock(), on_event=events.append, scenario="idle")
    s.start(run_threads=False)
    s.pin, s.events, s.broken, s.made = pin, events, broken, made  # type: ignore[attr-defined]
    yield s
    s.stop()


def test_reload_exclusive_releases_old_before_starting_new(world):
    assert world.pin.owner == "argus"
    world.reload("haptics", impl="team")
    assert world.pin.owner == "team" and world.status()["haptics"]["impl"] == "team"
    log = world.pin.log
    assert log.index("stop:argus") < log.index("start:team")
    assert "start-failed:team" not in log  # the old order would have hit the busy pin


def test_failed_reload_restarts_old_impl_and_says_so(world):
    world.broken.add("team")
    t0 = time.perf_counter()
    with pytest.raises(ModuleLoadError) as e:
        world.reload("haptics", impl="team")
    assert time.perf_counter() - t0 < 2.0
    msg = str(e.value)
    assert "team did not start" in msg and "previous impl argus was restarted and is running again" in msg
    assert world.pin.owner == "argus" and world.status()["haptics"]["impl"] == "argus"
    assert world.status()["haptics"]["health"]["ok"] is True
    world.haptics.set("top", 0.5)  # the proxy reaches the live (restored) driver
    assert world.module("haptics").level == 0.5


def test_reload_failure_when_old_cannot_come_back_is_reported(world):
    world.broken.update({"team", "argus"})
    with pytest.raises(ModuleLoadError) as e:
        world.reload("haptics", impl="team")
    assert "could NOT be restarted" in str(e.value)


def test_automatic_fallback_swaps_exclusive_hardware_in_order(world):
    world.made["argus"][0].ok = False  # driver turns unhealthy while still holding the pin
    world.check_once("haptics", selftest=False)
    assert world.status()["haptics"]["impl"] == "team" and world.status()["haptics"]["on_fallback"]
    assert world.pin.owner == "team"
    assert world.pin.log.index("stop:argus") < world.pin.log.index("start:team")
    [ev] = world.events
    assert ev["type"] == "module_fallback" and ev["to_impl"] == "team"


def test_fallback_that_cannot_start_is_reported_as_failure(world):
    world.broken.add("team")
    world.made["argus"][0].ok = False
    world.check_once("haptics", selftest=False)
    [ev] = world.events
    assert ev["to_impl"] is None and "did not start" in ev["reason"]
    assert world.status()["haptics"]["gave_up"]


def test_startup_with_failing_primary_uses_fallback(monkeypatch):
    pin, broken = Pin(), {"argus"}
    real = supmod.load_impl
    monkeypatch.setattr(supmod, "load_impl", lambda m, i, c: FakeDriver(i, pin, broken=i in broken) if m == "haptics"
                        else real(m, i, c))
    events: list[dict] = []
    s = Supervisor(make_cfg(haptics={"impl": "argus", "fallback": "team"}), SampleBus(), VirtualClock(),
                   on_event=events.append, scenario="idle")
    s.start(run_threads=False)
    try:
        assert s.status()["haptics"]["impl"] == "team" and events[0]["from_impl"] == "argus"
    finally:
        s.stop()
