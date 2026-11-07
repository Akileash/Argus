# Device on-disk event queue (v1)

PM-owned. The writer is the event manager (`device/argus/events/`, M2.3). The reader is the uploader (`device/argus/uploader.py`, M2.4). It is the only coupling between them. Spec §4.4: events stay on disk until the backend acknowledges them.

Root: `config.events.queue_dir`, default `device/var/queue/` (on the Pi, `/var/lib/argus/queue/` is fine, set by config).

```
<queue_dir>/
  <event_id>/                 # event_id = uuid4, same as ArgusEvent.event_id
    event.json                # ArgusEvent (contracts/event.schema.json), t0/detected_at already UTC
    clip.mp4                  # optional (absent for hat_removed / module_fallback)
    trace.json                # optional, ArgusSensorTrace (contracts/sensor_trace.schema.json)
    READY                     # empty marker, written LAST (atomic rename). Uploader ignores dirs without it.
    upload_state.json         # owned by the uploader: {attempts, last_error, next_attempt_at, server_status}
```

Rules:
- The writer builds everything in `<queue_dir>/.tmp-<event_id>/`, then `os.rename`s it to `<event_id>/`, then creates `READY`. Partial events are never visible.
- `event.json.media[]` lists exactly the files present, with `bytes` and `sha256` computed over the final files.
- Monotonic → UTC mapping happens once in the writer: `utc = wall_at_write - (mono_at_write - t_ns)`.
- The uploader deletes the directory only after `POST /api/events/{id}/complete` returns 200, or after `POST /api/events` returns `status: ready` with no uploads.
- Ordering: oldest `t0` first. Backoff runs per event, so one failing event doesn't block the others.
- Disk guard: if the queue holds more than `events.queue_max_mb` (default 2048), the writer drops the **oldest clip.mp4** (keeping event.json + trace.json) and records `"clip_dropped": true` in `upload_state.json`. It never drops event rows.
