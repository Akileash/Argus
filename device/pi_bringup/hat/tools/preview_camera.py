#!/usr/bin/env python3
"""Live Pi Cam MJPEG preview in a browser (LAN / Tailscale).

  python3 -m hat.tools.preview_camera
  python3 -m hat.tools.preview_camera --port 8080 --size 640x480

Open the printed URL on your phone/laptop. Ctrl+C to stop.
"""

from __future__ import annotations

import argparse
import io
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Condition

PAGE = """<!DOCTYPE html>
<html><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Argus live camera</title>
<style>
  html,body{margin:0;background:#111;color:#eee;font-family:system-ui,sans-serif}
  header{padding:.75rem 1rem;background:#1a1a1a}
  img{display:block;width:100%;max-width:1280px;margin:0 auto;background:#000}
</style>
</head><body>
<header>Argus live preview — refresh if the image stalls</header>
<img src="/stream.mjpg" alt="live camera"/>
</body></html>
"""


def _lan_ips() -> list[str]:
    ips: list[str] = []
    try:
        ips.extend(subprocess.check_output(["hostname", "-I"], text=True).split())
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        out = subprocess.check_output(["tailscale", "ip", "-4"], text=True).strip()
        if out and out not in ips:
            ips.insert(0, out)
    except (OSError, subprocess.SubprocessError):
        pass
    seen: set[str] = set()
    ordered: list[str] = []
    for ip in ips:
        if ip not in seen and ":" not in ip:
            seen.add(ip)
            ordered.append(ip)
    return ordered or ["127.0.0.1"]


class StreamingOutput(io.BufferedIOBase):
    def __init__(self) -> None:
        self.frame: bytes | None = None
        self.condition = Condition()

    def write(self, buf: bytes) -> int:  # type: ignore[override]
        with self.condition:
            self.frame = buf
            self.condition.notify_all()
        return len(buf)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Live Argus Pi Cam MJPEG preview")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument(
        "--size",
        default="640x480",
        help="WxH preview size (default 640x480; keep modest for Wi‑Fi)",
    )
    p.add_argument("--fps", type=int, default=15)
    args = p.parse_args(argv)

    try:
        w_s, h_s = args.size.lower().split("x", 1)
        size = (int(w_s), int(h_s))
    except ValueError:
        print(f"Bad --size {args.size!r}; use e.g. 640x480", file=sys.stderr)
        return 2

    try:
        from picamera2 import Picamera2
        from picamera2.encoders import MJPEGEncoder
        from picamera2.outputs import FileOutput
    except ImportError as e:
        print(f"picamera2 not available: {e}", file=sys.stderr)
        return 1

    info = Picamera2.global_camera_info()
    if not info:
        print(
            "No cameras available.\n"
            "Reseat the Pi Cam ribbon (contacts toward HDMI on Pi 4),\n"
            "then check: rpicam-hello --list-cameras",
            file=sys.stderr,
        )
        return 1

    output = StreamingOutput()
    picam = Picamera2()
    video_cfg = picam.create_video_configuration(
        main={"size": size},
        controls={"FrameRate": float(args.fps)},
    )
    picam.configure(video_cfg)
    encoder = MJPEGEncoder(bitrate=4_000_000)
    picam.start_recording(encoder, FileOutput(output))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path in ("/", "/index.html"):
                body = PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path != "/stream.mjpg":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Age", "0")
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Pragma", "no-cache")
            self.send_header(
                "Content-Type", "multipart/x-mixed-replace; boundary=FRAME"
            )
            self.end_headers()
            try:
                while True:
                    with output.condition:
                        output.condition.wait()
                        frame = output.frame
                    if frame is None:
                        continue
                    self.wfile.write(b"--FRAME\r\n")
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(frame)))
                    self.end_headers()
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, fmt, *a):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % a))

    ips = _lan_ips()
    host = next((ip for ip in ips if ip.startswith("100.")), ips[0])
    httpd = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)

    print(f"Camera: {info[0].get('Model', info[0])}")
    print(f"Streaming {size[0]}x{size[1]} @{args.fps}fps")
    print()
    print("Open on your PC / phone:")
    print(f"  http://{host}:{args.port}/")
    for ip in ips:
        if ip != host:
            print(f"  (also) http://{ip}:{args.port}/")
    print("\nCtrl+C to stop.")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        try:
            picam.stop_recording()
            picam.close()
        except Exception:
            pass
        # give encoder threads a moment to unwind
        time.sleep(0.2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
