"""Output sinks: stdout NDJSON and append-only jsonl history files."""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set


class Sink:
    def emit(self, evt: Dict[str, Any]) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class StdoutSink(Sink):
    """Print one JSON object per line. Human-friendly prefixes for key events."""

    def __init__(self, quiet: bool = False) -> None:
        self.quiet = quiet
        self._lock = threading.Lock()

    def emit(self, evt: Dict[str, Any]) -> None:
        compact = json.dumps(evt, separators=(",", ":"))
        etype = evt.get("type")
        with self._lock:
            if self.quiet:
                print(compact, flush=True)
                return
            if etype == "wear_event":
                if evt.get("helmet_worn"):
                    print(f"HELMET ON (worn)  {compact}", flush=True)
                else:
                    print(f"HELMET OFF (not worn)  {compact}", flush=True)
            elif etype == "air_sample":
                print(
                    f"AIR     co2={evt.get('co2_ppm')} ppm  "
                    f"temp={evt.get('temp_c')} C  rh={evt.get('rh_pct')}%  {compact}",
                    flush=True,
                )
            elif etype == "hello":
                print(f"HELLO   {compact}", flush=True)
            elif etype == "sensor_recover":
                result = "ok" if evt.get("ok") else "FAILED (check wiring)"
                print(
                    f"RECOVER sensor reset {result}  recoveries={evt.get('recoveries')}",
                    flush=True,
                )
            elif etype == "sample":
                if "helmet_worn" in evt:
                    label = "HELMET ON " if evt.get("helmet_worn") else "HELMET OFF"
                    print(
                        f"{label}  mm={evt.get('distance_mm')} "
                        f"near={evt.get('near')} status={evt.get('status')} "
                        f"sensor_ok={evt.get('sensor_ok')}",
                        flush=True,
                    )
                else:
                    print(
                        f"sample  co2={evt.get('co2_ppm')}  "
                        f"temp={evt.get('temp_c')}  rh={evt.get('rh_pct')}  "
                        f"sensor_ok={evt.get('sensor_ok')}",
                        flush=True,
                    )
            else:
                print(compact, flush=True)


class JsonlSink(Sink):
    """Append selected event types to a .jsonl file (flush every line)."""

    def __init__(
        self,
        path: Path,
        types: Optional[Iterable[str]] = None,
        log_samples: bool = False,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._types: Optional[Set[str]] = set(types) if types is not None else None
        self.log_samples = log_samples
        self._lock = threading.Lock()
        self._fh = open(self.path, "a", encoding="utf-8")

    def emit(self, evt: Dict[str, Any]) -> None:
        etype = evt.get("type")
        if etype == "sample" and not self.log_samples:
            return
        if self._types is not None and etype not in self._types:
            if not (etype == "sample" and self.log_samples):
                return
        line = json.dumps(evt, separators=(",", ":")) + "\n"
        with self._lock:
            self._fh.write(line)
            self._fh.flush()

    def close(self) -> None:
        with self._lock:
            try:
                self._fh.close()
            except Exception:
                pass


class FanoutSink(Sink):
    def __init__(self, sinks: List[Sink]) -> None:
        self.sinks = list(sinks)

    def emit(self, evt: Dict[str, Any]) -> None:
        for s in self.sinks:
            try:
                s.emit(evt)
            except Exception as exc:
                print(f"# sink error: {exc}", file=sys.stderr)

    def close(self) -> None:
        for s in self.sinks:
            try:
                s.close()
            except Exception:
                pass
