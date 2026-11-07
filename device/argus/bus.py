"""L1 sample bus: per-stream time-indexed ring buffers with fan-out (spec §4, §5.1).

Streams are plain strings (``imu``, ``range``, ``tof``, ``env``). Each holds samples sorted by
``t_ns`` for ``history_s`` seconds (spec §4: "about 40 s", default 35 s). Thread safe.
"""

from __future__ import annotations

import bisect
import logging
import threading
from collections.abc import Callable, Iterable
from typing import Any

log = logging.getLogger(__name__)

STREAMS: tuple[str, ...] = ("imu", "range", "tof", "env")
Subscriber = Callable[[Any], None]


class _Ring:
    """Sorted-by-time sample list; old samples are trimmed in batches (amortised O(1))."""

    def __init__(self, history_ns: int) -> None:
        self.history_ns = history_ns
        self.ts: list[int] = []
        self.items: list[Any] = []
        self.lock = threading.Lock()

    def append(self, item: Any) -> None:
        t = item.t_ns
        with self.lock:
            if self.ts and t < self.ts[-1]:  # rare out-of-order sample: keep the ring sorted
                i = bisect.bisect_right(self.ts, t)
                self.ts.insert(i, t)
                self.items.insert(i, item)
            else:
                self.ts.append(t)
                self.items.append(item)
            slack = self.history_ns // 10
            if self.ts[0] < self.ts[-1] - self.history_ns - slack:
                k = bisect.bisect_left(self.ts, self.ts[-1] - self.history_ns)
                del self.ts[:k]
                del self.items[:k]

    def window(self, t0: int, t1: int) -> list[Any]:
        with self.lock:
            lo = bisect.bisect_left(self.ts, t0)
            hi = bisect.bisect_right(self.ts, t1)
            return self.items[lo:hi]


class SampleBus:
    def __init__(self, history_s: float = 35.0, streams: Iterable[str] = STREAMS) -> None:
        self.history_s = history_s
        self._rings = {s: _Ring(int(history_s * 1e9)) for s in streams}
        self._subs: dict[str, list[Subscriber]] = {s: [] for s in self._rings}
        self._sub_lock = threading.Lock()

    def _ring(self, stream: str) -> _Ring:
        try:
            return self._rings[stream]
        except KeyError:
            raise KeyError(f"unknown stream {stream!r}; known: {sorted(self._rings)}") from None

    def publish(self, stream: str, samples: Iterable[Any]) -> int:
        """Append samples (each needs ``t_ns``) and fan out to subscribers. Returns the count."""
        ring = self._ring(stream)
        n = 0
        with self._sub_lock:
            subs = list(self._subs[stream])
        for s in samples:
            ring.append(s)
            n += 1
            for cb in subs:
                try:
                    cb(s)
                except Exception:  # a bad subscriber must never stall the bus
                    log.exception("bus subscriber failed on stream %s", stream)
        return n

    def window(self, stream: str, t_start_ns: int, t_end_ns: int) -> list[Any]:
        """Samples with ``t_start_ns <= t_ns <= t_end_ns``, oldest first."""
        return self._ring(stream).window(t_start_ns, t_end_ns)

    def latest(self, stream: str) -> Any | None:
        ring = self._ring(stream)
        with ring.lock:
            return ring.items[-1] if ring.items else None

    def subscribe(self, stream: str, callback: Subscriber) -> Callable[[], None]:
        """Register ``callback(sample)``; returns an unsubscribe function."""
        self._ring(stream)
        with self._sub_lock:
            self._subs[stream].append(callback)

        def unsubscribe() -> None:
            with self._sub_lock:
                if callback in self._subs[stream]:
                    self._subs[stream].remove(callback)

        return unsubscribe

    def __len__(self) -> int:
        return sum(len(r.ts) for r in self._rings.values())
