"""Parametrization + harness for the contract suite."""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from argus.contracts import MODULE_NAMES, ModuleContext, MonotonicClock
from argus.supervisor import load_impl
from hardware.mock.clock import VirtualClock
from hardware.mock.scenario import load_scenario
from hardware.replay.recording import write_recording


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "module" in metafunc.fixturenames and "impl" in metafunc.fixturenames:
        modules = metafunc.config.getoption("--module") or list(MODULE_NAMES)
        impls = metafunc.config.getoption("--impl") or ["mock"]
        for m in modules:
            if m not in MODULE_NAMES:
                raise pytest.UsageError(f"--module {m!r}: choose from {list(MODULE_NAMES)}")
        params = [(m, i) for m in modules for i in impls]
        metafunc.parametrize("module,impl", params, ids=[f"{m}-{i}" for m, i in params])


@dataclass
class Harness:
    module: str
    impl: str
    clock: Any
    inst: Any
    virtual: bool

    def advance(self, seconds: float) -> None:
        """Let time pass: instant on a virtual clock, real sleep on real hardware."""
        if self.virtual:
            self.clock.advance(seconds)
        else:
            time.sleep(seconds)

    def collect(self, seconds: float, step: float = 0.05) -> list[Any]:
        out: list[Any] = []
        for _ in range(round(seconds / step)):
            self.advance(step)
            out.extend(self.inst.read())
        return out


@pytest.fixture
def make_dut(module: str, impl: str, tmp_path: Path):
    """Factory returning a (not yet started) Harness; started instances are stopped at teardown."""
    made: list[Harness] = []

    def make(scenario: str = "idle") -> Harness:
        virtual = impl in ("mock", "replay")
        clock = VirtualClock() if virtual else MonotonicClock()
        options: dict[str, Any] = {}
        if module == "camera":
            options["cache_dir"] = str(tmp_path.parent / "argus-camera-cache")
        if impl == "replay":
            sc = load_scenario(scenario)
            from hardware.mock.env import MockEnv
            from hardware.mock.hat_on import MockHatOn
            from hardware.mock.imu import MockImu  # build a recording from the mock streams
            from hardware.mock.range import MockRange

            vclock = VirtualClock()
            ctx = ModuleContext(vclock, {}, scenario)
            mocks = [m(ctx) for m in (MockImu, MockRange, MockHatOn, MockEnv)]
            for m in mocks:
                m.start()
            samples: list[Any] = []
            for _ in range(int(sc.duration_s / 0.05)):
                vclock.advance(0.05)
                for m in mocks:
                    samples.extend(m.read())
            options["file"] = str(write_recording(tmp_path / "rec.jsonl", samples))
        inst = load_impl(module, impl, ModuleContext(clock, options, scenario))
        h = Harness(module, impl, clock, inst, virtual)
        made.append(h)
        return h

    yield make
    for h in made:
        with contextlib.suppress(Exception):
            h.inst.stop()


@pytest.fixture
def dut(make_dut) -> Harness:
    h = make_dut()
    h.inst.start()
    return h
