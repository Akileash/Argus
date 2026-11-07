"""Team env adapter skeleton (spec §5.5). Not pasted yet: ``create`` raises so the supervisor falls back.

Contract: co2 in ppm, temperature in deg C, relative humidity in %; ~0.2 Hz.

Steps (see README.md): paste their script next to this file UNCHANGED, then fill in ONE pattern:
  (a) ``TeamEnv``           import their code and call it in a thread, or
  (b) ``TeamEnvSubprocess`` run their blocking ``while True: print(...)`` script and parse stdout.
Then make ``create`` return it, and run: ``pytest tests/contract --module env --impl team``.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from argus.contracts import EnvSample, EnvSensor, ModuleContext  # noqa: F401 (EnvSample used once pasted)
from hardware.team._adapter_base import StreamAdapter, SubprocessAdapter

SCRIPT = Path(__file__).with_name("their_script.py")  # <- the pasted script, unchanged


class TeamEnv(StreamAdapter):
    """Pattern (a): import-and-call in a thread."""

    def run(self, emit: Callable[[Any], None]) -> None:
        # from hardware.team.env import their_script as their_module   # <- their code, unchanged
        raise NotImplementedError("team impl for env not pasted yet - see README")
        # while not self.stopping:
        #     co2, temp, rh = their_module.read_scd41()
        #     emit(EnvSample(self.now(), co2, temp, rh))
        #     # stamp with self.now(); one sample = EnvSample(t_ns, co2_ppm, temp_c, rh_pct)


class TeamEnvSubprocess(SubprocessAdapter):
    """Pattern (b): run their script as a subprocess and parse its stdout.

    Example line: ``co2=655 temp=22.3 rh=41.2``
    """

    command = (sys.executable, "-u", str(SCRIPT))

    def parse_line(self, line: str, t_ns: int) -> list[Any]:
        raise NotImplementedError("team impl for env not pasted yet - see README")
        # Example (delete the raise above and adapt):
        # k = dict(p.split("=") for p in line.split())   # adapt to THEIR output format
        # return [EnvSample(t_ns, float(k["co2"]), float(k["temp"]), float(k["rh"]))]


def create(ctx: ModuleContext) -> EnvSensor:
    raise NotImplementedError("team impl for env not pasted yet — see README")
    # return TeamEnv(ctx)             # pattern (a)
    # return TeamEnvSubprocess(ctx)   # pattern (b)
