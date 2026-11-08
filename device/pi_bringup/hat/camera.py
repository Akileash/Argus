"""Pi Camera rolling buffer: keep ~PRE seconds, dump + POST seconds on fall.

Uses picamera2 CircularOutput. Saves H.264 then remuxes to MP4 with ffmpeg
when available. Never raises into the IMU loop — failures are logged only.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from hat import config as cfg


class ClipRecorder:
    """
    Continuously buffers the last CAM_PRE_S seconds of video.
    On capture(event_id): flush pre-roll + record CAM_POST_S more → clips/.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._picam = None
        self._encoder = None
        self._circular = None
        self._busy = False
        self._started = False
        self.enabled = bool(getattr(cfg, "CAM_ENABLE", True))

    def start(self) -> None:
        if not self.enabled:
            print("[camera] disabled in config (CAM_ENABLE=False)")
            return
        try:
            from picamera2 import Picamera2
            from picamera2.encoders import H264Encoder
            from picamera2.outputs import CircularOutput
        except ImportError as e:
            print(f"[camera] picamera2 not available: {e}")
            self.enabled = False
            return

        cfg.CLIPS_DIR.mkdir(parents=True, exist_ok=True)
        frames = max(30, int(cfg.CAM_FPS * cfg.CAM_PRE_S) + cfg.CAM_FPS)

        try:
            picam = Picamera2()
            video_cfg = picam.create_video_configuration(
                main={"size": tuple(cfg.CAM_SIZE)},
                controls={"FrameRate": float(cfg.CAM_FPS)},
            )
            picam.configure(video_cfg)
            encoder = H264Encoder(bitrate=int(cfg.CAM_BITRATE))
            circular = CircularOutput(buffersize=frames)
            picam.start_recording(encoder, circular)
        except Exception as e:
            print(f"[camera] failed to start: {e}")
            self.enabled = False
            return

        self._picam = picam
        self._encoder = encoder
        self._circular = circular
        self._started = True
        print(
            f"[camera] rolling buffer started "
            f"({cfg.CAM_PRE_S:.0f}s pre / {cfg.CAM_POST_S:.0f}s post, "
            f"{cfg.CAM_SIZE[0]}x{cfg.CAM_SIZE[1]} @{cfg.CAM_FPS}fps)"
        )

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                return
            try:
                if self._picam is not None:
                    self._picam.stop_recording()
                    self._picam.close()
            except Exception as e:
                print(f"[camera] stop error: {e}")
            self._picam = None
            self._encoder = None
            self._circular = None
            self._started = False
            print("[camera] stopped")

    def capture_async(self, event_id: str) -> None:
        """Kick off pre+post dump in a background thread (non-blocking)."""
        if not self.enabled or not self._started:
            return
        t = threading.Thread(
            target=self._capture_worker,
            args=(event_id,),
            name=f"clip-{event_id[:8]}",
            daemon=True,
        )
        t.start()

    def capture(self, event_id: str) -> Optional[Path]:
        """Blocking capture: returns path to .mp4 (or .h264 if remux fails)."""
        if not self.enabled or not self._started:
            return None
        with self._lock:
            if self._busy:
                print(f"[camera] already capturing — skip {event_id}")
                return None
            self._busy = True
        try:
            path = self._capture(event_id)
            if path is not None:
                print(f"[camera] saved clip → {path}", flush=True)
            return path
        except Exception as e:
            print(f"[camera] capture failed for {event_id}: {e}", flush=True)
            return None
        finally:
            with self._lock:
                self._busy = False

    def _capture_worker(self, event_id: str) -> None:
        self.capture(event_id)

    def _capture(self, event_id: str) -> Optional[Path]:
        circ = self._circular
        if circ is None:
            return None

        cfg.CLIPS_DIR.mkdir(parents=True, exist_ok=True)
        h264 = cfg.CLIPS_DIR / f"{event_id}.h264"
        mp4 = cfg.CLIPS_DIR / f"{event_id}.mp4"

        print(
            f"[camera] dumping ~{cfg.CAM_PRE_S:.0f}s pre-roll + "
            f"recording {cfg.CAM_POST_S:.0f}s post for {event_id}…",
            flush=True,
        )

        # Point circular buffer at a file: buffered frames flush as new ones arrive.
        circ.fileoutput = str(h264)
        time.sleep(float(cfg.CAM_POST_S))
        # Flush any remaining buffered frames, close the file, then resume buffering.
        try:
            circ.stop()
        except Exception as e:
            print(f"[camera] circular stop: {e}")
        try:
            circ.fileoutput = None
            circ.start()
        except Exception as e:
            print(f"[camera] circular restart: {e}")

        if not h264.exists() or h264.stat().st_size < 1000:
            print(f"[camera] H.264 too small or missing: {h264}")
            return None

        remuxed = self._remux_mp4(h264, mp4)
        if remuxed is not None:
            try:
                h264.unlink(missing_ok=True)
            except OSError:
                pass
            return remuxed
        return h264

    @staticmethod
    def _remux_mp4(h264: Path, mp4: Path) -> Optional[Path]:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return None
        cmd = [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-framerate",
            str(int(cfg.CAM_FPS)),
            "-i",
            str(h264),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(mp4),
        ]
        try:
            subprocess.run(cmd, check=True, timeout=60)
        except (subprocess.SubprocessError, OSError) as e:
            print(f"[camera] ffmpeg remux failed: {e}")
            return None
        if mp4.exists() and mp4.stat().st_size > 0:
            return mp4
        return None
