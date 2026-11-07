"""Team hat_on adapter skeleton (spec §5.5). Not pasted yet: ``create`` raises so the supervisor falls back.

Contract: mm; status ok | out_of_range | error (VL53L0X returns ~8190 mm when nothing is in range).

Steps (see README.md): paste their script next to this file UNCHANGED, then fill in ONE pattern:
  (a) ``TeamHatOn``           import their code and call it in a thread, or
  (b) ``TeamHatOnSubprocess`` run their blocking ``while True: print(...)`` script and parse stdout.
Then make ``create`` return it, and run: ``pytest tests/contract --module hat_on --impl team``.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from argus.contracts import HatOnSensor, ModuleContext, TofSample  # noqa: F401 (TofSample used once pasted)
from hardware.team._adapter_base import StreamAdapter, SubprocessAdapter

SCRIPT = Path(__file__).with_name("their_script.py")  # <- the pasted script, unchanged


class TeamHatOn(StreamAdapter):
    """Pattern (a): import-and-call in a thread."""

    def run(self, emit: Callable[[Any], None]) -> None:
        # from hardware.team.hat_on import their_script as their_module   # <- their code, unchanged
        raise NotImplementedError("team impl for hat_on not pasted yet - see README")
        # while not self.stopping:
        #     mm = their_module.read_mm()
        #     ok = mm is not None and mm < 2000
        #     emit(TofSample(self.now(), float(mm) if ok else None, "ok" if ok else "out_of_range"))
        #     # stamp with self.now(); one sample = TofSample(t_ns, range_mm, status)


class TeamHatOnSubprocess(SubprocessAdapter):
    """Pattern (b): run their script as a subprocess and parse its stdout.

    Example line: ``distance_mm=63 status=0``
    """

    command = (sys.executable, "-u", str(SCRIPT))

    def parse_line(self, line: str, t_ns: int) -> list[Any]:
        raise NotImplementedError("team impl for hat_on not pasted yet - see README")
        # Example (delete the raise above and adapt):
        # mm = float(line.split("distance_mm=")[1].split()[0])  # adapt to THEIR output format
        # ok = mm < 2000
        # return [TofSample(t_ns, mm if ok else None, "ok" if ok else "out_of_range")]


def create(ctx: ModuleContext) -> HatOnSensor:
    raise NotImplementedError("team impl for hat_on not pasted yet — see README")
    # return TeamHatOn(ctx)             # pattern (a)
    # return TeamHatOnSubprocess(ctx)   # pattern (b)
