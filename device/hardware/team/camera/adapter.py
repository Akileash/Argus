"""Team camera adapter skeleton (spec §3.1, §5.5). Not pasted yet: ``create`` raises.

Contract: keep a rolling buffer (>= 30 s) stamped with the shared monotonic clock and implement
``save_clip(t_start_ns, t_end_ns, path) -> ClipInfo`` writing a real MP4 with ``ffmpeg -c copy``.

  (a) import their capture code in a thread (e.g. picamera2 encoder writing 1 s H.264/TS segments
      into a ring directory, recording ``segment -> clock.now_ns()`` as each one closes), or
  (b) run their ``rpicam-vid``/ffmpeg command as a subprocess that writes the same segments.
``save_clip`` then concats the segments covering the window (see ``hardware/mock/camera`` for a
complete, working reference of exactly this flow).
Run: ``pytest tests/contract --module camera --impl team``.
"""

from __future__ import annotations

from argus.contracts import Camera, ClipInfo, Health, ModuleContext


class TeamCamera:
    def __init__(self, ctx: ModuleContext) -> None:
        self.ctx = ctx
        self._errors = 0

    def start(self) -> None:
        raise NotImplementedError("start their capture here (pattern a thread or pattern b subprocess)")

    def stop(self) -> None:
        pass

    def save_clip(self, t_start_ns: int, t_end_ns: int, path: str) -> ClipInfo:
        raise NotImplementedError("concat the ring segments covering the window with ffmpeg -c copy")

    def health(self) -> Health:
        return Health(False, None, self._errors, "not implemented")


def create(ctx: ModuleContext) -> Camera:
    raise NotImplementedError("team impl for camera not pasted yet — see README")
    # return TeamCamera(ctx)
