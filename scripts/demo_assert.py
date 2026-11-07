"""Assertions for scripts/demo-e2e.sh: checks the events a demo run created, through the Supabase REST API.

Run with the device venv (httpx + jsonschema):  uv run --project device python scripts/demo_assert.py ...
Prints one PASS/FAIL table; exit 0 only if every check of every scenario passed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import httpx
import jsonschema

REPO = Path(__file__).resolve().parent.parent
TARGET_S = 300.0  # spec: report ready within 5 minutes
EXPECTED_CLASS = {
    "struck_by_near_miss": "near_miss",
    "struck_by_contact": "incident",
    "head_impact": "incident",
    "fall": "incident",
    "hat_knocked_off": "incident",
    "hat_removed": "ppe_log",
}
TERMINAL = {"ready", "failed"}


def parse_ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


class Api:
    def __init__(self, url: str, key: str):
        self.url = url.rstrip("/")
        self.h = {"apikey": key, "Authorization": f"Bearer {key}"}
        self.c = httpx.Client(timeout=30.0)

    def rest(self, table: str, **params: str) -> list[dict]:
        r = self.c.get(f"{self.url}/rest/v1/{table}", params=params, headers=self.h)
        r.raise_for_status()
        return r.json()

    def object(self, path: str) -> httpx.Response:
        return self.c.get(f"{self.url}/storage/v1/object/event-media/{path}", headers=self.h)


def probe_duration(data: bytes) -> float:
    with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
        f.write(data)
        f.flush()
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
             "-of", "json", f.name],
            capture_output=True, text=True, check=True,
        ).stdout
    info = json.loads(out)
    if not any(s.get("codec_type") == "video" for s in info.get("streams", [])):
        raise ValueError("no video stream")
    return float(info["format"]["duration"])


def check_event(api: Api, ev: dict, validator: jsonschema.Draft202012Validator, want_type: str) -> tuple[list[tuple[str, bool, str]], float | None]:
    out: list[tuple[str, bool, str]] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        out.append((name, bool(ok), detail))

    eid = ev["id"]
    add("type", ev["type"] == want_type, ev["type"])
    add("device_class", ev["device_class"] == EXPECTED_CLASS[want_type], ev["device_class"])
    add("worker resolved", ev["worker_id"] is not None, str(ev["worker_id"]))
    add("status ready", ev["status"] == "ready", f"{ev['status']} {ev.get('error') or ''}".strip())

    media = api.rest("media", event_id=f"eq.{eid}", select="*")
    reports = api.rest("reports", event_id=f"eq.{eid}", select="*")
    analyses = api.rest("analyses", event_id=f"eq.{eid}", select="id,stage,model")

    if want_type == "hat_removed":
        add("no media", not media, f"{len(media)} rows")
        add("no report", not reports, f"{len(reports)} rows")
    else:
        kinds = sorted(m["kind"] for m in media)
        add("media rows clip+trace", kinds == ["clip", "sensor_trace"], str(kinds))
        add("media confirmed", bool(media) and all(m["confirmed"] for m in media))
        for m in media:
            r = api.object(m["storage_path"])
            add(f"object present ({m['kind']})", r.status_code == 200 and len(r.content) > 0 and
                (m["bytes"] is None or len(r.content) == int(m["bytes"])), f"HTTP {r.status_code} {len(r.content)} B")
            if m["kind"] == "clip" and r.status_code == 200:
                try:
                    d = probe_duration(r.content)
                    add("clip is playable MP4 ~30 s", 25.0 <= d <= 35.0, f"{d:.1f} s")
                except Exception as e:  # noqa: BLE001
                    add("clip is playable MP4 ~30 s", False, f"ffprobe: {e}")
        add("one draft report", len(reports) == 1 and reports[0]["status"] == "draft", f"{[r['status'] for r in reports]}")
        if reports:
            rj = reports[0]["report_json"]
            errs = sorted(validator.iter_errors(rj), key=lambda e: list(e.path))
            add("report matches report.schema.json", not errs,
                "; ".join(f"{'/'.join(map(str, e.path))}: {e.message[:80]}" for e in errs[:3]))
            add("location_on_site placeholder", rj.get("header", {}).get("location_on_site") == "Location to be determined",
                str(rj.get("header", {}).get("location_on_site")))
            so = rj.get("sign_off", {})
            add("sign_off all null", bool(so) and all(v is None for v in so.values()), json.dumps(so))
        add("analyses row logged", len(analyses) >= 1, f"{len(analyses)} rows")

    ready_at = parse_ts(ev["updated_at"]) if ev["status"] == "ready" else None
    secs = ready_at - parse_ts(ev["t0"]) if ready_at else None
    if secs is not None:
        add(f"ready within {TARGET_S/60:.0f} min of trigger", secs <= TARGET_S, f"{secs:.1f} s")
    return out, secs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--serial", required=True)
    ap.add_argument("--since", required=True, help="ISO time: only events created after this count")
    ap.add_argument("--wait-s", type=float, default=420.0)
    ap.add_argument("--summary-json", help="write {scenario: {event_id, seconds}} here")
    ap.add_argument("expect", nargs="+", help="scenario:event_type")
    a = ap.parse_args()

    api = Api(a.url, a.key)
    schema = json.loads((REPO / "contracts" / "report.schema.json").read_text())
    validator = jsonschema.Draft202012Validator(schema)
    dev = api.rest("devices", serial=f"eq.{a.serial}", select="id")
    if not dev:
        print(f"FAIL: device {a.serial} not found in the database")
        return 1
    dev_id = dev[0]["id"]

    rows: list[tuple[str, str, bool, str]] = []  # scenario, check, ok, detail
    timings: dict[str, float | None] = {}
    summary: dict[str, dict] = {}
    deadline = time.time() + a.wait_s
    for item in a.expect:
        scenario, want_type = item.split(":")
        ev = None
        while True:
            found = api.rest("events", device_id=f"eq.{dev_id}", type=f"eq.{want_type}",
                             created_at=f"gte.{a.since}", select="*", order="created_at.desc", limit="1")
            if found:
                ev = found[0]
                if ev["status"] in TERMINAL:
                    break
            if time.time() > deadline:
                break
            time.sleep(2.0)
        if ev is None:
            rows.append((scenario, f"event {want_type} created", False, "no such event reached the backend"))
            timings[scenario] = None
            continue
        checks, secs = check_event(api, ev, validator, want_type)
        rows.extend((scenario, n, ok, d) for n, ok, d in checks)
        timings[scenario] = secs
        summary[scenario] = {"event_id": ev["id"], "type": want_type, "seconds": secs}

    w = max(len(r[1]) for r in rows) if rows else 10
    cur = None
    for sc, name, ok, detail in rows:
        if sc != cur:
            print(f"\n  {sc}")
            cur = sc
        print(f"    {'PASS' if ok else 'FAIL'}  {name:<{w}}  {detail}")
    print("\n  trigger (t0) -> ready, target <= 300 s")
    for sc, secs in timings.items():
        print(f"    {sc:<24} {'n/a' if secs is None else f'{secs:6.1f} s'}")
    failed = [r for r in rows if not r[2]]
    print(f"\n  {len(rows) - len(failed)}/{len(rows)} checks passed")
    if a.summary_json:
        Path(a.summary_json).write_text(json.dumps(summary))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
