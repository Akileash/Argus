"""Uploader against an in-process fake backend (httpx.MockTransport)."""

from __future__ import annotations

import hashlib
import json
import random
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from argus.uploader import BackendSettings, Uploader

SERIAL = "ARG-0002"
TOKEN = "tok-1"


class FakeBackend:
    def __init__(self) -> None:
        self.events: dict[str, dict[str, Any]] = {}
        self.stored: dict[str, bytes] = {}
        self.confirmed: set[str] = set()
        self.log: list[tuple[str, str]] = []  # (method, path-ish)
        self.post_order: list[str] = []
        self.valid_tokens = {TOKEN}
        self.fail_post: list[httpx.Response | Exception] = []  # popped one per POST /api/events
        self.fail_complete: list[httpx.Response | Exception] = []
        self.fail_put: list[httpx.Response] = []
        self.put_headers: list[httpx.Headers] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        self.log.append((req.method, path))
        if req.method == "PUT":
            data = req.read()
            if self.fail_put:
                return self.fail_put.pop(0)
            self.put_headers.append(req.headers)
            self.stored[path] = data
            return httpx.Response(200, json={"Key": path})
        if req.headers.get("authorization") != f"Bearer {TOKEN}" and \
                req.headers.get("authorization", "")[7:] not in self.valid_tokens:
            return httpx.Response(401, json={"error": {"code": "unauthorized", "message": "bad token"}})
        if req.method == "POST" and path == "/api/events":
            if self.fail_post:
                f = self.fail_post.pop(0)
                if isinstance(f, Exception):
                    raise f
                return f
            ev = json.loads(req.content)
            if ev["device_serial"] != SERIAL:
                return httpx.Response(403, json={"error": {"code": "serial_mismatch", "message": "nope"}})
            self.post_order.append(ev["event_id"])
            short = ev["type"] == "hat_removed"
            row = self.events.setdefault(ev["event_id"], {"ev": ev, "status": "ready" if short else "received"})
            created = row.pop("_seen", None) is None
            row["_seen"] = True
            uploads = []
            if not short:
                row["status"] = row["status"] if row["status"] not in ("received",) else "uploading"
                if row["status"] == "uploading":
                    for m in ev["media"]:
                        key = f"/{ev['event_id']}/{m['filename']}"
                        if key not in self.confirmed:
                            uploads.append({"kind": m["kind"], "filename": m["filename"],
                                            "signed_url": f"http://storage.test{key}?token=x", "expires_at": "x"})
            return httpx.Response(201 if created else 200,
                                  json={"event_id": ev["event_id"], "status": row["status"], "uploads": uploads})
        if req.method == "POST" and path.endswith("/complete"):
            if self.fail_complete:
                f = self.fail_complete.pop(0)
                if isinstance(f, Exception):
                    raise f
                return f
            eid = path.split("/")[3]
            row = self.events[eid]
            body = json.loads(req.content)
            problems = []
            for m in row["ev"]["media"]:
                data = self.stored.get(f"/{eid}/{m['filename']}")
                if data is None:
                    problems.append(f"{m['filename']}: object not found")
                elif len(data) != m["bytes"] or hashlib.sha256(data).hexdigest() != m["sha256"]:
                    problems.append(f"{m['filename']}: mismatch")
                elif m["filename"] not in body["uploaded"]:
                    problems.append(f"{m['filename']}: not reported")
            if problems:
                return httpx.Response(409, json={"error": {"code": "upload_incomplete", "message": "x"},
                                                 "problems": problems})
            row["status"] = "uploaded"
            return httpx.Response(200, json={"status": "uploaded"})
        return httpx.Response(404, json={"error": {"code": "not_found", "message": path}})


def make_event(queue: Path, *, etype: str = "fall_impact", t0: datetime | None = None, media: bool = True,
               serial: str = SERIAL) -> Path:
    eid = str(uuid.uuid4())
    t0 = t0 or datetime.now(UTC)
    d = queue / eid
    d.mkdir(parents=True)
    files = []
    if media:
        clip = b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 40
        trace = json.dumps({"schema_version": "1"}).encode()
        (d / "clip.mp4").write_bytes(clip)
        (d / "trace.json").write_bytes(trace)
        files = [
            {"kind": "clip", "camera": "front", "filename": "clip.mp4", "content_type": "video/mp4",
             "bytes": len(clip), "sha256": hashlib.sha256(clip).hexdigest(), "duration_s": 30.0},
            {"kind": "sensor_trace", "filename": "trace.json", "content_type": "application/json",
             "bytes": len(trace), "sha256": hashlib.sha256(trace).hexdigest()},
        ]
    ev = {"schema_version": "1", "event_id": eid, "device_serial": serial, "type": etype,
          "device_class": "near_miss", "t0": t0.isoformat().replace("+00:00", "Z"),
          "detected_at": t0.isoformat().replace("+00:00", "Z"), "window": {"pre_s": 20, "post_s": 10},
          "metrics": {"hat_on": True}, "media": files, "firmware": "test"}
    (d / "event.json").write_text(json.dumps(ev))
    (d / "READY").touch()
    return d


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def env(tmp_path: Path):
    q = tmp_path / "queue"
    q.mkdir()
    be = FakeBackend()
    clock = Clock()
    box = {"s": BackendSettings("http://backend.test", TOKEN, 5.0, 60.0, SERIAL)}
    up = Uploader(q, lambda: box["s"], transport=be.transport(), now=clock, rng=random.Random(1))
    return q, be, clock, box, up


def test_happy_path(env):
    q, be, _, _, up = env
    d = make_event(q)
    assert up.drain_once() == {"uploaded": 1, "retry": 0, "rejected": 0, "waiting": 0, "skipped": 0}
    assert not d.exists()
    eid = d.name
    assert be.events[eid]["status"] == "uploaded"
    assert be.stored[f"/{eid}/clip.mp4"].startswith(b"\x00\x00\x00\x18ftyp")
    ct = {h["content-type"] for h in be.put_headers}
    assert ct == {"video/mp4", "application/json"}
    assert all("authorization" not in h for h in be.put_headers)  # signed URL only
    assert [m for m, _ in be.log] == ["POST", "PUT", "PUT", "POST"]


def test_idempotent_replay_after_crash_between_put_and_complete(env):
    q, be, _, _, up = env
    d = make_event(q)
    be.fail_complete.append(httpx.ConnectError("boom"))  # "crash" after the PUTs
    assert up.drain_once()["retry"] == 1
    assert d.exists()
    st = json.loads((d / "upload_state.json").read_text())
    assert st["attempts"] == 1 and "network" in st["last_error"]
    # A fresh process (new Uploader) resumes from disk state only.
    clock2 = Clock()
    clock2.t += 1000
    up2 = Uploader(q, lambda: BackendSettings("http://backend.test", TOKEN), transport=be.transport(), now=clock2)
    assert up2.drain_once()["uploaded"] == 1
    assert not d.exists()
    assert be.events[d.name]["status"] == "uploaded"
    assert len([1 for m, _ in be.log if m == "POST" and _ == "/api/events"]) == 2  # replayed POST is harmless


def test_409_upload_incomplete_is_retried(env):
    q, be, clock, _, up = env
    d = make_event(q)
    problem = httpx.Response(409, json={"error": {"code": "upload_incomplete", "message": "x"},
                                        "problems": ["clip.mp4: object not found"]})
    be.fail_complete.append(problem)
    assert up.drain_once()["retry"] == 1
    assert "upload_incomplete" in json.loads((d / "upload_state.json").read_text())["last_error"]
    clock.t += 120
    assert up.drain_once()["uploaded"] == 1


def test_5xx_backoff_is_exponential_capped_and_persisted(env):
    q, be, clock, box, up = env
    d = make_event(q)
    be.fail_post.extend([httpx.Response(503, json={"error": {"code": "x", "message": "y"}})] * 12)
    delays = []
    for _ in range(9):
        up.drain_once()
        st = json.loads((d / "upload_state.json").read_text())
        delays.append(st["next_attempt_at"] - clock.t)
        # still waiting: a second pass must not hit the server
        n = len(be.log)
        assert up.drain_once()["waiting"] == 1 and len(be.log) == n
        clock.t = st["next_attempt_at"] + 0.001
    assert 0.7 < delays[0] < 1.3
    assert 1.4 < delays[1] < 2.6
    assert delays[2] > delays[1] * 1.0
    assert max(delays) <= 60.0 and delays[-1] > 40  # reached the cap region
    assert json.loads((d / "upload_state.json").read_text())["attempts"] == 9
    be.fail_post.clear()
    assert up.drain_once()["uploaded"] == 1


def test_network_error_retries(env):
    q, be, _, _, up = env
    make_event(q)
    be.fail_post.append(httpx.ConnectError("down"))
    assert up.drain_once()["retry"] == 1


def test_failing_event_does_not_block_others(env):
    q, be, clock, _, up = env
    t = datetime.now(UTC)
    a = make_event(q, t0=t - timedelta(seconds=30))
    b = make_event(q, t0=t - timedelta(seconds=20))
    be.fail_post.append(httpx.Response(500, json={}))  # first POST (event a) fails
    assert up.drain_once() == {"uploaded": 1, "retry": 1, "rejected": 0, "waiting": 0, "skipped": 0}
    assert a.exists() and not b.exists()


def test_401_rereads_config_and_waits(env):
    q, be, clock, box, up = env
    d = make_event(q)
    box["s"] = BackendSettings("http://backend.test", "stale-token")
    assert up.drain_once()["skipped"] == 1
    n = len(be.log)
    clock.t += 500
    assert up.drain_once()["skipped"] == 1 and len(be.log) == n  # same token: no hammering
    assert "401" in (up.status()["last_error"] or "")
    box["s"] = BackendSettings("http://backend.test", TOKEN)  # `argus-cli pair` rewrote the config
    assert up.drain_once(ignore_backoff=True)["uploaded"] == 1
    assert not d.exists()


def test_4xx_rejects_and_keeps_files(env):
    q, be, clock, box, up = env
    box["s"] = BackendSettings("http://backend.test", TOKEN, 5.0, 60.0, "ARG-9999")  # config serial is wrong
    d = make_event(q)  # server answers 403 serial_mismatch for the (current but wrong) serial
    assert up.drain_once() == {"uploaded": 0, "retry": 0, "rejected": 1, "waiting": 0, "skipped": 0}
    st = json.loads((d / "upload_state.json").read_text())
    assert st["server_status"] == "rejected" and "serial_mismatch" in st["last_error"]
    assert (d / "clip.mp4").exists()
    n = len(be.log)
    clock.t += 1000
    assert up.drain_once()["rejected"] == 0 and len(be.log) == n  # never retried
    s = up.status()
    assert s["rejected"] == 1 and s["pending"] == 0


def test_event_written_before_pairing_uploads_with_current_serial(env):
    q, be, _, _, up = env
    d = make_event(q, serial="unprovisioned")  # written while device.serial was still blank
    assert up.drain_once()["uploaded"] == 1 and not d.exists()
    assert be.events[next(iter(be.events))]["ev"]["device_serial"] == SERIAL


def test_serial_paired_while_running_is_picked_up(env):
    q, be, clock, box, up = env
    box["s"] = BackendSettings("http://backend.test", TOKEN, 5.0, 60.0, "")  # paired without a known serial
    d = make_event(q, serial="unprovisioned")
    assert up.drain_once() == {"uploaded": 0, "retry": 1, "rejected": 0, "waiting": 0, "skipped": 0}
    st = json.loads((d / "upload_state.json").read_text())
    assert st.get("server_status") != "rejected" and "serial_mismatch" in st["last_error"]
    box["s"] = BackendSettings("http://backend.test", TOKEN, 5.0, 60.0, SERIAL)  # `argus-cli pair` filled it
    clock.t += 1000
    assert up.drain_once()["uploaded"] == 1 and not d.exists()


def test_422_rejected(env):
    q, be, _, _, up = env
    d = make_event(q)
    be.fail_post.append(httpx.Response(422, json={"error": {"code": "validation_failed", "message": "bad"},
                                                  "errors": ["x"]}))
    assert up.drain_once()["rejected"] == 1
    assert json.loads((d / "upload_state.json").read_text())["server_status"] == "rejected"


def test_short_circuit_hat_removed_no_uploads(env):
    q, be, _, _, up = env
    d = make_event(q, etype="hat_removed", media=False)
    assert up.drain_once()["uploaded"] == 1
    assert not d.exists()
    assert [m for m, _ in be.log] == ["POST"]  # no PUT, no /complete


def test_oldest_t0_first_and_ready_only(env):
    q, be, _, _, up = env
    t = datetime.now(UTC)
    newest = make_event(q, t0=t)
    oldest = make_event(q, t0=t - timedelta(minutes=5))
    mid = make_event(q, t0=t - timedelta(minutes=1))
    unready = make_event(q, t0=t - timedelta(hours=1))
    (unready / "READY").unlink()
    (q / ".tmp-abc").mkdir()
    up.drain_once()
    assert be.post_order == [oldest.name, mid.name, newest.name]
    assert unready.exists()


def test_dropped_clip_is_omitted_from_media(env):
    q, be, _, _, up = env
    d = make_event(q)
    (d / "clip.mp4").unlink()
    (d / "upload_state.json").write_text(json.dumps({"clip_dropped": True}))
    assert up.drain_once()["uploaded"] == 1
    assert [m["filename"] for m in be.events[d.name]["ev"]["media"]] == ["trace.json"]


def test_state_preserves_writer_keys(env):
    q, be, _, _, up = env
    d = make_event(q)
    (d / "upload_state.json").write_text(json.dumps({"clip_dropped": False, "custom": 1}))
    be.fail_post.append(httpx.Response(500, json={}))
    up.drain_once()
    st = json.loads((d / "upload_state.json").read_text())
    assert st["custom"] == 1 and st["attempts"] == 1


def test_unpaired_idles_and_never_exits(tmp_path: Path):
    q = tmp_path / "q"
    q.mkdir()
    be = FakeBackend()
    box = {"s": BackendSettings("", "")}
    up = Uploader(q, lambda: box["s"], transport=be.transport())
    d = make_event(q)
    up.start()
    try:
        time.sleep(0.4)
        assert up._thread is not None and up._thread.is_alive()
        assert be.log == [] and d.exists()
        assert up.next_wakeup_s() == 10.0
        assert up.status()["paired"] is False
    finally:
        up.stop()
    assert not up._thread.is_alive()


def test_run_forever_picks_up_pairing_and_uploads(tmp_path: Path):
    q = tmp_path / "q"
    q.mkdir()
    be = FakeBackend()
    box = {"s": BackendSettings("", "")}
    up = Uploader(q, lambda: box["s"], transport=be.transport())
    d = make_event(q)
    up._stop = threading.Event()
    box["s"] = BackendSettings("http://backend.test", TOKEN)
    up.start()
    try:
        for _ in range(50):
            if not d.exists():
                break
            time.sleep(0.1)
    finally:
        up.stop()
    assert not d.exists()


def test_status_line(env):
    q, be, clock, _, up = env
    assert "queue_depth=0" in up.status_line()
    d = make_event(q, t0=datetime.fromtimestamp(clock.t - 90, UTC))
    be.fail_post.append(httpx.Response(500, json={"error": {"code": "boom", "message": "m"}}))
    up.drain_once()
    line = up.status_line()
    assert "queue_depth=1" in line and "oldest_age=90s" in line and "boom" in line and d.name in line
