"""L4 uploader (spec §4.4, contracts/api.md, contracts/device_queue.md).

Scans the on-disk queue (READY dirs only, oldest ``t0`` first) and for each event does
``POST /api/events`` -> ``PUT`` signed URLs -> ``POST /complete`` -> delete the directory on 200.
Per-event exponential backoff (persisted in ``upload_state.json``) so a failing event never blocks others.
Crash-safe: every step is idempotent on the server, and the directory is only deleted after the ack.
"""

from __future__ import annotations

import json
import logging
import os
import random
import shutil
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from argus.config import DEFAULT_CONFIG_PATH, ArgusConfig, ConfigError, load_config

log = logging.getLogger("argus.uploader")

STATE_FILE = "upload_state.json"
READY_FILE = "READY"
UNPAIRED_RECHECK_S = 10.0
POLL_S = 2.0
BASE_BACKOFF_S = 1.0
CHUNK = 256 * 1024
DEFAULT_QUEUE_DIR = Path(__file__).resolve().parents[1] / "var" / "queue"
PENDING = ("received", "uploading")


@dataclass(frozen=True)
class BackendSettings:
    base_url: str = ""
    token: str = ""
    timeout_s: float = 30.0
    max_backoff_s: float = 60.0
    serial: str = ""  # current device.serial; written into every event body at upload time

    @property
    def paired(self) -> bool:
        return bool(self.base_url.strip() and self.token.strip())


def settings_from_config(cfg: ArgusConfig) -> BackendSettings:
    b = cfg.backend
    return BackendSettings(b.base_url.strip().rstrip("/"), b.device_token.strip(), b.timeout_s, b.max_backoff_s,
                           (cfg.device.serial or "").strip())


def queue_dir_from_config(cfg: ArgusConfig) -> Path:
    q = getattr(cfg.events, "queue_dir", None)  # owned by the event manager's config section
    p = Path(q) if q else DEFAULT_QUEUE_DIR
    return p if p.is_absolute() else Path(__file__).resolve().parents[1] / p


def config_provider(path: Path | str | None) -> Callable[[], BackendSettings]:
    """Re-reads the YAML only when its mtime changes (cheap to call every loop)."""
    cache: dict[str, Any] = {"key": None, "val": BackendSettings()}

    def get() -> BackendSettings:
        try:
            p = Path(path or os.environ.get("ARGUS_CONFIG") or DEFAULT_CONFIG_PATH)
            key = (str(p), p.stat().st_mtime_ns)
        except OSError:
            key = None
        if key is None or key != cache["key"]:
            try:
                cache["val"] = settings_from_config(load_config(path))
            except ConfigError as e:
                log.warning("config unreadable, keeping previous backend settings: %s", e)
            cache["key"] = key
        return cache["val"]

    return get


class _Retry(Exception):
    """Transient failure: back off and try again."""


class _Reject(Exception):
    """Permanent failure for this event (4xx)."""


class _Unauthorized(Exception):
    pass


def _err_text(r: httpx.Response) -> str:
    try:
        j = r.json()
        e = j.get("error", {})
        txt = f"{e.get('code', '')}: {e.get('message', '')}"
        if j.get("problems"):
            txt += f" {j['problems']}"
        if j.get("errors"):
            txt += f" {j['errors']}"
        return f"HTTP {r.status_code} {txt}".strip()
    except Exception:  # noqa: BLE001 - any unparsable body: fall back to the raw text
        return f"HTTP {r.status_code} {r.text[:200]}"


class Uploader:
    def __init__(
        self,
        queue_dir: Path | str,
        settings: Callable[[], BackendSettings],
        *,
        transport: httpx.BaseTransport | None = None,
        now: Callable[[], float] = time.time,
        rng: random.Random | None = None,
    ) -> None:
        self.queue_dir = Path(queue_dir)
        self._settings = settings
        self._transport = transport
        self._now = now
        self._rng = rng or random.Random()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._auth_failed_token: str | None = None
        self._warned_unpaired = False
        self.last_error: str | None = None
        self._serial_sent_is_current = True

    # ------------------------------------------------------------------ queue scanning
    def _event_dirs(self) -> list[Path]:
        if not self.queue_dir.is_dir():
            return []
        return [d for d in self.queue_dir.iterdir()
                if d.is_dir() and not d.name.startswith(".") and (d / READY_FILE).exists()]

    @staticmethod
    def _read_json(p: Path) -> dict[str, Any]:
        try:
            v = json.loads(p.read_text())
            return v if isinstance(v, dict) else {}
        except (OSError, ValueError):
            return {}

    def read_state(self, d: Path) -> dict[str, Any]:
        return self._read_json(d / STATE_FILE)

    def _write_state(self, d: Path, **updates: Any) -> dict[str, Any]:
        st = self.read_state(d)  # preserve keys owned by the writer (e.g. clip_dropped)
        st.update(updates)
        tmp = d / (STATE_FILE + ".tmp")
        tmp.write_text(json.dumps(st))
        os.replace(tmp, d / STATE_FILE)
        return st

    @staticmethod
    def _t0(d: Path, ev: dict[str, Any]) -> float:
        try:
            return datetime.fromisoformat(str(ev["t0"])).astimezone(UTC).timestamp()
        except (KeyError, ValueError):
            try:
                return (d / READY_FILE).stat().st_mtime
            except OSError:
                return 0.0

    def _ordered(self) -> list[tuple[float, Path]]:
        out = [(self._t0(d, self._read_json(d / "event.json")), d) for d in self._event_dirs()]
        out.sort(key=lambda x: (x[0], x[1].name))
        return out

    def status(self) -> dict[str, Any]:
        items = self._ordered()
        rejected = pending = 0
        last_err: tuple[float, str] | None = None
        for _, d in items:
            st = self.read_state(d)
            if st.get("server_status") == "rejected":
                rejected += 1
            else:
                pending += 1
            if st.get("last_error"):
                at = float(st.get("last_error_at") or 0)
                if last_err is None or at >= last_err[0]:
                    last_err = (at, f"{d.name}: {st['last_error']}")
        oldest_age = max(0.0, self._now() - items[0][0]) if items else None
        return {"paired": self._settings().paired, "depth": len(items), "pending": pending, "rejected": rejected,
                "oldest_age_s": oldest_age, "last_error": last_err[1] if last_err else self.last_error}

    def status_line(self) -> str:
        s = self.status()
        age = "-" if s["oldest_age_s"] is None else f"{s['oldest_age_s']:.0f}s"
        return (f"paired={'yes' if s['paired'] else 'no'} queue_depth={s['depth']} pending={s['pending']} "
                f"rejected={s['rejected']} oldest_age={age} last_error={s['last_error'] or '-'}")

    # ------------------------------------------------------------------ one event
    def _backoff_delay(self, attempts: int, cap: float) -> float:
        base = min(cap, BASE_BACKOFF_S * 2 ** max(0, attempts - 1))
        return min(cap, base * self._rng.uniform(0.75, 1.25))

    def _request(self, c: httpx.Client, method: str, url: str, s: BackendSettings, **kw: Any) -> httpx.Response:
        try:
            r = c.request(method, url, headers={"Authorization": f"Bearer {s.token}"}, **kw)
        except httpx.HTTPError as e:
            raise _Retry(f"network: {type(e).__name__}: {e}") from None
        if r.status_code in (200, 201):
            return r
        if r.status_code == 401:
            raise _Unauthorized(_err_text(r))
        if r.status_code >= 500 or r.status_code == 409:
            raise _Retry(_err_text(r))
        text = _err_text(r)
        if r.status_code == 403 and "serial_mismatch" in text and not self._serial_sent_is_current:
            # We could not send a trustworthy serial (blank in config: paired without verification or not
            # paired yet). Not the event's fault: keep it queued and retry once the config has a serial.
            raise _Retry(f"{text} (local device.serial is blank; run `argus-cli pair`)")
        raise _Reject(text)

    @staticmethod
    def _file_iter(path: Path) -> Iterator[bytes]:
        with path.open("rb") as f:
            while chunk := f.read(CHUNK):
                yield chunk

    def _put(self, c: httpx.Client, url: str, path: Path, content_type: str) -> None:
        try:
            r = c.put(url, content=self._file_iter(path),
                      headers={"Content-Type": content_type, "Content-Length": str(path.stat().st_size)})
        except httpx.HTTPError as e:
            raise _Retry(f"network (PUT {path.name}): {type(e).__name__}: {e}") from None
        except OSError as e:
            raise _Reject(f"cannot read {path.name}: {e}") from None
        if r.status_code not in (200, 201):
            # Signed URLs expire and storage hiccups happen: re-POST gets fresh URLs, so always retry.
            raise _Retry(f"PUT {path.name}: {_err_text(r)}")

    def upload_event(self, d: Path, s: BackendSettings) -> None:
        """Full flow for one event dir. Deletes it on success. Raises _Retry/_Reject/_Unauthorized."""
        ev = self._read_json(d / "event.json")
        if not ev.get("event_id"):
            raise _Reject("event.json missing or invalid")
        # Only describe files that actually exist (the writer may have dropped clip.mp4 under disk pressure).
        media = [m for m in ev.get("media", []) if (d / m["filename"]).is_file()]
        body = {**ev, "media": media}
        # The serial in event.json was read when the event was written. A device paired while the service
        # was running (or an event written before pairing: "unprovisioned") would be refused with 403
        # serial_mismatch, so always send the serial the device has *now*.
        if s.serial:
            body["device_serial"] = s.serial
        types = {m["filename"]: m["content_type"] for m in media}
        self._serial_sent_is_current = bool(s.serial)
        with httpx.Client(transport=self._transport, timeout=s.timeout_s, follow_redirects=False) as c:
            res = self._request(c, "POST", f"{s.base_url}/api/events", s, json=body).json()
            uploads = res.get("uploads") or []
            if not uploads and res.get("status") not in PENDING:
                shutil.rmtree(d, ignore_errors=True)  # short-circuit (ready) or already complete
                return
            for u in uploads:
                name = u["filename"]
                if name not in types:
                    raise _Reject(f"server asked for unknown file {name!r}")
                self._put(c, u["signed_url"], d / name, types[name])
            r = self._request(c, "POST", f"{s.base_url}/api/events/{ev['event_id']}/complete", s,
                              json={"uploaded": sorted(types)})
            log.info("event %s uploaded (status=%s)", ev["event_id"], r.json().get("status"))
        shutil.rmtree(d, ignore_errors=True)

    # ------------------------------------------------------------------ drain loop
    def drain_once(self, *, ignore_backoff: bool = False) -> dict[str, int]:
        """One pass over due events, oldest first. Returns counts."""
        counts = {"uploaded": 0, "retry": 0, "rejected": 0, "waiting": 0, "skipped": 0}
        s = self._settings()
        if not s.paired:
            return counts
        if self._auth_failed_token == s.token:
            counts["skipped"] = len(self._event_dirs())
            return counts
        self._auth_failed_token = None
        for _, d in self._ordered():
            if self._stop.is_set():
                break
            st = self.read_state(d)
            if st.get("server_status") == "rejected":
                continue
            if not ignore_backoff and float(st.get("next_attempt_at") or 0) > self._now():
                counts["waiting"] += 1
                continue
            try:
                self.upload_event(d, s)
                counts["uploaded"] += 1
            except _Unauthorized as e:
                self._record(d, st, str(e), s)
                self._auth_failed_token = s.token
                self.last_error = f"401 unauthorized: waiting for a new token ({e})"
                log.error("backend rejected the device token; pausing uploads until the config changes")
                counts["skipped"] += 1
                break
            except _Retry as e:
                self._record(d, st, str(e), s)
                counts["retry"] += 1
            except _Reject as e:
                log.error("REJECTED event %s, will not retry (files kept in %s): %s", d.name, d, e)
                if d.is_dir():
                    self._write_state(d, server_status="rejected", last_error=str(e), last_error_at=self._now())
                self.last_error = f"{d.name}: {e}"
                counts["rejected"] += 1
            except Exception as e:  # never let one bad event kill the thread
                log.exception("unexpected error uploading %s", d.name)
                self._record(d, st, f"internal: {type(e).__name__}: {e}", s)
                counts["retry"] += 1
        return counts

    def _record(self, d: Path, st: dict[str, Any], err: str, s: BackendSettings) -> None:
        attempts = int(st.get("attempts") or 0) + 1
        delay = self._backoff_delay(attempts, s.max_backoff_s)
        log.warning("event %s attempt %d failed (%s); retry in %.1fs", d.name, attempts, err, delay)
        if d.is_dir():
            self._write_state(d, attempts=attempts, last_error=err, last_error_at=self._now(),
                              next_attempt_at=self._now() + delay)
        self.last_error = f"{d.name}: {err}"

    def next_wakeup_s(self) -> float:
        """Seconds until something is due (bounded by POLL_S)."""
        s = self._settings()
        if not s.paired or self._auth_failed_token == s.token:
            return UNPAIRED_RECHECK_S
        nxt = POLL_S
        now = self._now()
        for _, d in self._ordered():
            st = self.read_state(d)
            if st.get("server_status") != "rejected":
                nxt = min(nxt, max(0.05, float(st.get("next_attempt_at") or 0) - now))
        return nxt

    def run_forever(self) -> None:
        """Never returns until stop(). Unpaired => idle quietly, re-check config every 10 s."""
        while not self._stop.is_set():
            try:
                if not self._settings().paired:
                    if not self._warned_unpaired:
                        log.info("uploader idle: device not paired (run `argus-cli pair`); rechecking every %.0fs",
                                 UNPAIRED_RECHECK_S)
                        self._warned_unpaired = True
                else:
                    self._warned_unpaired = False
                    self.drain_once()
                wait = self.next_wakeup_s()
            except Exception:
                log.exception("uploader loop error")
                wait = POLL_S
            self._stop.wait(wait)

    # ------------------------------------------------------------------ thread control
    def start(self) -> None:
        self._thread = threading.Thread(target=self.run_forever, name="argus-uploader", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)


def start_uploader(config_path: Path | str | None) -> Callable[[], None]:
    """Start the background uploader for ``argus-cli run``. Returns a stop function."""
    cfg = load_config(config_path)
    up = Uploader(queue_dir_from_config(cfg), config_provider(config_path))
    up.start()
    return up.stop
