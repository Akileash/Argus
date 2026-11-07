"""Team imu adapter skeleton (spec §5.5). Not pasted yet: ``create`` raises so the supervisor falls back.

Contract: accel in g (|a| ~ 1 at rest), gyro in deg/s, hat frame x fwd / y left / z up; >= 400 Hz.

Steps (see README.md): paste their script next to this file UNCHANGED, then fill in ONE pattern:
  (a) ``TeamImu``           import their code and call it in a thread, or
  (b) ``TeamImuSubprocess`` run their blocking ``while True: print(...)`` script and parse stdout.
Then make ``create`` return it, and run: ``pytest tests/contract --module imu --impl team``.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from argus.contracts import Imu, ImuSample, ModuleContext  # noqa: F401 (ImuSample used once pasted)
from hardware.team._adapter_base import StreamAdapter, SubprocessAdapter

SCRIPT = Path(__file__).with_name("their_script.py")  # <- the pasted script, unchanged


class TeamImu(StreamAdapter):
    """Pattern (a): import-and-call in a thread."""

    def run(self, emit: Callable[[Any], None]) -> None:
        # from hardware.team.imu import their_script as their_module   # <- their code, unchanged
        raise NotImplementedError("team impl for imu not pasted yet - see README")
        # while not self.stopping:
        #     ax, ay, az, gx, gy, gz = their_module.read_once()  # convert units here (m/s^2 -> g, rad/s -> deg/s)
        #     # stamp with self.now(); one sample = ImuSample(t_ns, ax, ay, az, gx, gy, gz)


class TeamImuSubprocess(SubprocessAdapter):
    """Pattern (b): run their script as a subprocess and parse its stdout.

    Example line: ``ax=0.01 ay=-0.00 az=1.01 gx=0.4 gy=-0.3 gz=0.2``
    """

    command = (sys.executable, "-u", str(SCRIPT))

    def parse_line(self, line: str, t_ns: int) -> list[Any]:
        raise NotImplementedError("team impl for imu not pasted yet - see README")
        # Example (delete the raise above and adapt):
        # k = dict(p.split("=") for p in line.split())   # adapt to THEIR output format
        # return [ImuSample(t_ns, *(float(k[n]) for n in ("ax", "ay", "az", "gx", "gy", "gz")))]


def create(ctx: ModuleContext) -> Imu:
    raise NotImplementedError("team impl for imu not pasted yet — see README")
    # return TeamImu(ctx)             # pattern (a)
    # return TeamImuSubprocess(ctx)   # pattern (b)
