"""Helpers for ``team`` adapters (spec §5.5): thread-and-queue and subprocess-stdout patterns.

An adapter must keep the contract rules of ``argus/contracts.py``: stamp samples with the shared
clock (``self.now()``), convert to contract units, never raise out of ``read()``, and keep
``read()`` non-blocking (the team code runs in a background thread/process).
"""

from __future__ import annotations

import logging
import queue
import subprocess
import threading
from collections.abc import Callable, Sequence
from typing import Any, ClassVar

from argus.contracts import Health, ModuleContext

log = logging.getLogger(__name__)


class StreamAdapter:
    """Pattern (a): run the team's code in a thread. Override ``run(emit)``."""

    def __init__(self, ctx: ModuleContext) -> None:
        self.ctx = ctx
        self._q: queue.Queue[Any] = queue.Queue(maxsize=100_000)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._errors = 0
        self._last_t_ns: int | None = None
        self._note = ""

    def now(self) -> int:
        return self.ctx.clock.now_ns()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def run(self, emit: Callable[[Any], None]) -> None:
        raise NotImplementedError("override run(emit) with the team's read loop")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._guard, name=type(self).__name__, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _emit(self, sample: Any) -> None:
        try:
            self._q.put_nowait(sample)
            self._last_t_ns = self.now()
        except queue.Full:
            self._errors += 1

    def _guard(self) -> None:
        while not self._stop.is_set():
            try:
                self.run(self._emit)
                return
            except Exception as e:
                self._errors += 1
                self._note = f"{type(e).__name__}: {e}"
                log.exception("team adapter %s crashed", type(self).__name__)
                self._stop.wait(0.5)

    def read(self) -> list[Any]:
        out: list[Any] = []
        try:
            while True:
                out.append(self._q.get_nowait())
        except queue.Empty:
            pass
        return out

    def health(self) -> Health:
        alive = bool(self._thread and self._thread.is_alive())
        age = None if self._last_t_ns is None else (self.now() - self._last_t_ns) / 1e6
        return Health(alive, age, self._errors, self._note)


class SubprocessAdapter(StreamAdapter):
    """Pattern (b): run a blocking ``while True: print(...)`` script and parse its stdout.

    Set ``command`` and implement ``parse_line``. The team's script needs no changes.
    """

    command: ClassVar[Sequence[str]] = ()

    def parse_line(self, line: str, t_ns: int) -> list[Any]:
        raise NotImplementedError("convert one stdout line into contract samples (or [] to skip it)")

    def run(self, emit: Callable[[Any], None]) -> None:
        proc = subprocess.Popen(self.command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1)
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if self.stopping:
                    break
                t_ns = self.now()  # stamp on arrival: this is the best timestamp stdout gives us
                try:
                    for s in self.parse_line(line.strip(), t_ns):
                        emit(s)
                except Exception:  # noqa: BLE001
                    self._errors += 1
                    log.warning("unparseable line from team script: %r", line.strip())
            if not self.stopping:
                raise RuntimeError(f"team script exited with code {proc.poll()}")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.kill()
