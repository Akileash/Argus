"""Injectable virtual clock so mock/replay modules and tests run in accelerated time."""

from __future__ import annotations

import threading


class VirtualClock:
    """Satisfies ``argus.contracts.Clock``. Time only moves when ``advance`` is called."""

    def __init__(self, start_ns: int = 1_000_000_000) -> None:
        self._t = start_ns
        self._lock = threading.Lock()

    def now_ns(self) -> int:
        with self._lock:
            return self._t

    def advance(self, seconds: float) -> int:
        with self._lock:
            self._t += int(seconds * 1e9)
            return self._t
