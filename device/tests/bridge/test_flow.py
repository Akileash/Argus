"""Bridge end to end without the Pi: events.jsonl in, our on-disk queue out."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from argus.bridge.clips import ClipPolicy
from argus.events.queue import validate_event, validate_trace

from .conftest import FAST, T0, Clock, make_bridge, make_cfg, queued, settle


def qdir(cfg, eid) -> Path:
    return Path(cfg.events.queue_dir) / eid


def test_fall_with_clip_is_queued_with_clip_and_trace(cfg, hat):
    ev = hat.fall()
    hat.mp4(ev["event_id"])
    b = make_bridge(cfg)
    b.state.offset = 0  # (first run would start at the end)
    settle(b)
    q = queued(cfg)
    assert list(q) == [ev["event_id"]]
    doc = q[ev["event_id"]]
    validate_event(doc)
    assert [m["kind"] for m in doc["media"]] == ["clip", "sensor_trace"]
    assert doc["device_serial"] == "ARG-0001" and doc["type"] == "fall"
    assert (qdir(cfg, ev["event_id"]) / "clip.mp4").stat().st_size == 5000
    validate_trace(json.loads((qdir(cfg, ev["event_id"]) / "trace.json").read_text()))
    assert doc["window"]["pre_s"] == 20.0  # clip length falls back to 30 s when ffprobe cannot read it


def test_late_clip_is_waited_for(cfg, hat):
    b = make_bridge(cfg, from_start=True)
    ev = hat.fall()
    t = hat.mp4_late(ev["event_id"], 0.5)
    settle(b)
    t.join()
    assert [m["kind"] for m in queued(cfg)[ev["event_id"]]["media"]] == ["clip", "sensor_trace"]


def test_h264_only_clip_goes_up_remuxed(cfg, hat, monkeypatch):
    from argus.bridge import clips

    def fake(src, dst):
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"m" * 3000)
        return True

    monkeypatch.setattr(clips, "remux_h264", fake)
    b = make_bridge(cfg, from_start=True)
    ev = hat.fall()
    (hat.clips / f"{ev['event_id']}.h264").write_bytes(b"h" * 5000)
    settle(b)
    assert (qdir(cfg, ev["event_id"]) / "clip.mp4").stat().st_size == 3000
    assert [m["kind"] for m in queued(cfg)[ev["event_id"]]["media"]] == ["clip", "sensor_trace"]


def test_no_clip_goes_up_without_video(tmp_path):
    cfg = make_cfg(tmp_path, clip_wait_s=0.3)
    from .conftest import Hat
    hat = Hat(cfg)
    b = make_bridge(cfg, from_start=True)
    b.policy = ClipPolicy(wait_s=0.3, poll_s=0.02, stable_s=0.1)
    ev = hat.fall()
    settle(b)
    doc = queued(cfg)[ev["event_id"]]
    assert [m["kind"] for m in doc["media"]] == ["sensor_trace"]
    assert doc["window"] == {"pre_s": 0.0, "post_s": 0.0}


def test_events_do_not_block_each_other_while_a_clip_is_awaited(cfg, hat):
    b = make_bridge(cfg, from_start=True)
    slow = hat.fall()  # its clip never comes in this test (until after the quick one is done)
    quick = hat.lof(T0 + timedelta(seconds=30))
    hat.mp4(quick["event_id"])
    b.step()
    import time
    deadline = time.monotonic() + 2
    while quick["event_id"] not in queued(cfg) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert quick["event_id"] in queued(cfg) and slow["event_id"] not in queued(cfg)
    hat.mp4(slow["event_id"])
    settle(b)
    assert slow["event_id"] in queued(cfg)


def test_line_of_fire_gets_a_clip_and_near_miss_type(cfg, hat):
    b = make_bridge(cfg, from_start=True)
    ev = hat.lof(side="right")
    hat.mp4(ev["event_id"])
    settle(b)
    doc = queued(cfg)[ev["event_id"]]
    assert (doc["type"], doc["device_class"], doc["metrics"]["sensor_id"]) == ("struck_by_near_miss", "near_miss", "R3")
    assert "clip" in [m["kind"] for m in doc["media"]]


def test_fall_after_near_miss_links_back(cfg, hat):
    b = make_bridge(cfg, from_start=True)
    nm = hat.lof(T0)
    fall = hat.fall(T0 + timedelta(seconds=1.2))
    hat.mp4(nm["event_id"])
    hat.mp4(fall["event_id"])
    settle(b)
    assert queued(cfg)[fall["event_id"]]["merged_triggers"] == [{"type": "struck_by_near_miss", "offset_s": -1.2}]


def test_air_sample_attached_only_when_fresh(cfg, hat):
    air = Path(cfg.bridge.air_log)
    air.write_text(
        json.dumps({"type": "wear_event", "received_at": T0.isoformat()}) + "\n"
        + json.dumps({"type": "air_sample", "co2_ppm": 800, "temp_c": 24.0, "rh_pct": 40.0, "sensor_sok": True,
                      "received_at": (T0 - timedelta(seconds=100)).isoformat()}) + "\n"
        + "not json\n")
    b = make_bridge(cfg, from_start=True)
    fresh = hat.fall(T0)
    stale = hat.fall(T0 + timedelta(seconds=1000))
    for e in (fresh, stale):
        hat.mp4(e["event_id"])
    settle(b)
    q = queued(cfg)
    assert q[fresh["event_id"]]["env"] == {"co2_ppm": 800.0, "temp_c": 24.0, "rh_pct": 40.0}
    assert q[stale["event_id"]]["env"] is None  # older than air_max_age_s


def test_junk_lines_unknown_types_and_duplicates_are_ignored(cfg, hat):
    b = make_bridge(cfg, from_start=True)
    ev = hat.fall()
    hat.mp4(ev["event_id"])
    with hat.events.open("a") as f:
        f.write("not json\n\n[1,2]\n")
        f.write(json.dumps({"type": "wear_event", "helmet_worn": True}) + "\n")
        f.write(json.dumps({"event_id": "x", "type": "impact", "timestamp": T0.isoformat()}) + "\n")
        f.write(json.dumps(ev) + "\n")  # the same event_id again
    settle(b)
    assert list(queued(cfg)) == [ev["event_id"]]


def test_partial_last_line_waits_for_its_newline(cfg, hat):
    b = make_bridge(cfg, from_start=True)
    ev = hat.fall()
    hat.mp4(ev["event_id"])
    text = hat.events.read_text()
    hat.events.write_text(text[:-1] + "")  # drop the trailing newline: the writer is mid-line
    settle(b)
    assert queued(cfg) == {}
    hat.events.write_text(text)
    settle(b)
    assert list(queued(cfg)) == [ev["event_id"]]


# ---------------------------------------------------------------- offset persistence
def test_first_run_starts_at_the_end_and_from_start_overrides(tmp_path):
    cfg = make_cfg(tmp_path)
    from .conftest import Hat
    hat = Hat(cfg)
    old = hat.fall()
    hat.mp4(old["event_id"])
    b = make_bridge(cfg)
    new = hat.fall()
    hat.mp4(new["event_id"])
    settle(b)
    assert list(queued(cfg)) == [new["event_id"]]  # the old test event is not uploaded
    cfg2 = make_cfg(tmp_path / "other")
    hat2 = Hat(cfg2)
    o2 = hat2.fall()
    hat2.mp4(o2["event_id"])
    b2 = make_bridge(cfg2, from_start=True)
    settle(b2)
    assert list(queued(cfg2)) == [o2["event_id"]]


def test_restart_resumes_at_the_saved_offset_without_duplicates(cfg, hat):
    b = make_bridge(cfg)
    e1 = hat.fall()
    hat.mp4(e1["event_id"])
    settle(b)
    b.stop()
    off = json.loads((Path(cfg.bridge.state_dir) / "bridge_state.json").read_text())["offset"]
    assert off == hat.events.stat().st_size
    for d in Path(cfg.events.queue_dir).iterdir():  # pretend the uploader already sent and deleted it
        import shutil
        shutil.rmtree(d)
    e2 = hat.fall(T0 + timedelta(seconds=60))
    hat.mp4(e2["event_id"])
    b2 = make_bridge(cfg)
    settle(b2)
    assert list(queued(cfg)) == [e2["event_id"]]  # e1 was not re-read


def test_seen_ids_survive_a_reset_offset(cfg, hat):
    b = make_bridge(cfg, from_start=True)
    e1 = hat.fall()
    hat.mp4(e1["event_id"])
    settle(b)
    b.stop()
    import shutil
    for d in Path(cfg.events.queue_dir).iterdir():
        shutil.rmtree(d)
    st = Path(cfg.bridge.state_dir) / "bridge_state.json"
    doc = json.loads(st.read_text())
    doc["offset"] = 0  # e.g. the state was edited, or the file was truncated and re-grown
    st.write_text(json.dumps(doc))
    b2 = make_bridge(cfg)
    settle(b2)
    assert queued(cfg) == {}


def test_stop_while_waiting_keeps_the_event_pending_and_the_next_start_sends_it(cfg, hat):
    b = make_bridge(cfg, from_start=True)
    b.policy = ClipPolicy(wait_s=30, poll_s=0.02, stable_s=0.1)
    ev = hat.fall()
    b.step()
    b.stop()  # no clip yet
    assert queued(cfg) == {}
    state = json.loads((Path(cfg.bridge.state_dir) / "bridge_state.json").read_text())
    assert ev["event_id"] in state["pending"]
    hat.mp4(ev["event_id"])
    b2 = make_bridge(cfg)
    b2.start()
    settle(b2)
    assert ev["event_id"] in queued(cfg)
    assert json.loads((Path(cfg.bridge.state_dir) / "bridge_state.json").read_text())["pending"] == {}


def test_truncated_events_file_restarts_at_zero(cfg, hat):
    b = make_bridge(cfg, from_start=True)
    e1 = hat.fall()
    hat.mp4(e1["event_id"])
    settle(b)
    assert list(queued(cfg)) == [e1["event_id"]]
    hat.events.write_text("")
    e2 = hat.fall(zone="a-longer-zone-name-so-the-regrown-file-is-not-the-same-size")
    hat.mp4(e2["event_id"])
    settle(b)
    assert {e1["event_id"], e2["event_id"]} == set(queued(cfg))


# ---------------------------------------------------------------- hat off timing (D6)
def run_off(cfg, hat, fall_to_off_arrival_s: float):
    """A fall at T0; HELMET OFF printed ``fall_to_off_arrival_s`` later. Returns (bridge, fall event, queue)."""
    clock = Clock(T0 + timedelta(seconds=0))
    b = make_bridge(cfg, clock, from_start=True)
    fall = hat.fall(T0)
    hat.mp4(fall["event_id"])
    settle(b)
    clock.advance(fall_to_off_arrival_s)
    b.on_tof(__import__("argus.bridge.launcher", fromlist=["TofLine"]).TofLine(False, "250", "False"))
    clock.advance(1.0)  # past the settle delay
    settle(b)
    return b, fall, queued(cfg)


def test_off_right_after_a_fall_is_a_knock_that_shares_the_clip(cfg, hat):
    # OFF is printed 3 s after the hat left; the hat left 0.5 s after the fall -> printed 3.5 s after it.
    _, fall, q = run_off(cfg, hat, 3.5)
    knock = next(d for d in q.values() if d["type"] == "hat_knocked_off")
    assert knock["device_class"] == "incident" and fall["event_id"] in q
    assert [m["kind"] for m in knock["media"]] == ["clip", "sensor_trace"]
    assert knock["metrics"]["peak_g"] == 3.42 and knock["metrics"]["hat_on"] is False
    assert knock["t0"] == "2026-11-08T22:00:00.500Z"
    assert (qdir(cfg, knock["event_id"]) / "clip.mp4").read_bytes() == (qdir(cfg, fall["event_id"]) / "clip.mp4").read_bytes()
    validate_event(knock)


def test_off_just_inside_and_outside_the_window(cfg, hat, tmp_path):
    # hat left exactly knock_window_s (2.0) after the fall -> still a knock
    _, _, q = run_off(cfg, hat, 5.0)
    assert any(d["type"] == "hat_knocked_off" for d in q.values())
    cfg2 = make_cfg(tmp_path / "late")
    from .conftest import Hat
    _, _, q2 = run_off(cfg2, Hat(cfg2), 5.3)  # left 2.3 s after the fall
    assert sorted(d["type"] for d in q2.values()) == ["fall", "hat_removed"]


def test_off_long_after_a_fall_is_hat_removed_without_media(cfg, hat):
    _, fall, q = run_off(cfg, hat, 60)
    removed = next(d for d in q.values() if d["type"] == "hat_removed")
    assert removed["device_class"] == "ppe_log" and removed["media"] == []
    assert removed["t0"] == "2026-11-08T22:00:57.000Z"  # arrival minus the 3 s ToF dwell
    assert not (qdir(cfg, removed["event_id"]) / "clip.mp4").exists()


def test_off_with_no_fall_is_hat_removed(cfg):
    clock = Clock()
    b = make_bridge(cfg, clock)
    from argus.bridge.launcher import TofLine
    b.on_tof(TofLine(True, "55", "True"))
    b.on_tof(TofLine(False, None, "False"))
    clock.advance(1)
    settle(b)
    assert [d["type"] for d in queued(cfg).values()] == ["hat_removed"]


def test_one_fall_is_knocked_off_at_most_once(cfg, hat):
    b, _, _ = run_off(cfg, hat, 3.5)
    from argus.bridge.launcher import TofLine
    b.on_tof(TofLine(False, None, "False"))
    b.mono_fn = b.mono_fn  # noqa: PLW0127
    b.wall_fn = lambda: T0 + timedelta(seconds=4.0)
    b.on_tof(TofLine(False, None, "False"))
    types = sorted(d["type"] for d in queued(cfg).values())
    assert types.count("hat_knocked_off") == 1
