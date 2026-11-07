"""Team range adapter skeleton (spec §5.5). Not pasted yet: ``create`` raises so the supervisor falls back.

Contract: mm (their script may print cm: multiply by 10); None = no echo; sensor_id in R1,R2,R3,R4,TOP.

Steps (see README.md): paste their script next to this file UNCHANGED, then fill in ONE pattern:
  (a) ``TeamRange``           import their code and call it in a thread, or
  (b) ``TeamRangeSubprocess`` run their blocking ``while True: print(...)`` script and parse stdout.
Then make ``create`` return it, and run: ``pytest tests/contract --module range --impl team``.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from argus.contracts import ModuleContext, RangeArray, RangeSample  # noqa: F401 (RangeSample used once pasted)
from hardware.team._adapter_base import StreamAdapter, SubprocessAdapter

SCRIPT = Path(__file__).with_name("their_script.py")  # <- the pasted script, unchanged


class TeamRange(StreamAdapter):
    """Pattern (a): import-and-call in a thread."""

    def run(self, emit: Callable[[Any], None]) -> None:
        # from hardware.team.range import their_script as their_module   # <- their code, unchanged
        raise NotImplementedError("team impl for range not pasted yet - see README")
        # while not self.stopping:
        #     readings = their_module.read_sweep()  # e.g. {"R1": 15.2, ...} in cm
        #     t = self.now()
        #     for sid, cm in readings.items():
        #         emit(RangeSample(t, sid, None if not cm else cm * 10.0))  # 0 / timeout -> None
        #     # stamp with self.now(); one sample = RangeSample(t_ns, sensor_id, range_mm)


class TeamRangeSubprocess(SubprocessAdapter):
    """Pattern (b): run their script as a subprocess and parse its stdout.

    Example line: ``R1=152.3 R2=148.0 R3=0 R4=151.2 TOP=801.5``
    """

    command = (sys.executable, "-u", str(SCRIPT))

    def parse_line(self, line: str, t_ns: int) -> list[Any]:
        raise NotImplementedError("team impl for range not pasted yet - see README")
        # Example (delete the raise above and adapt):
        # out = []
        # for part in line.split():                      # adapt to THEIR output format
        #     sid, _, val = part.partition("=")
        #     mm = float(val) * 10.0                     # their unit is cm here
        #     out.append(RangeSample(t_ns, sid, mm if mm > 0 else None))
        # return out


def create(ctx: ModuleContext) -> RangeArray:
    raise NotImplementedError("team impl for range not pasted yet — see README")
    # return TeamRange(ctx)             # pattern (a)
    # return TeamRangeSubprocess(ctx)   # pattern (b)
