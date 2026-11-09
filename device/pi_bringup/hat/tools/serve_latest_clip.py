#!/usr/bin/env python3
"""Serve the newest clip MP4 and print a browser URL.

  python3 -m hat.tools.serve_latest_clip
  python3 -m hat.tools.serve_latest_clip --port 8765
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

from hat import config as cfg


def _lan_ips() -> list[str]:
    ips: list[str] = []
    try:
        out = subprocess.check_output(["hostname", "-I"], text=True).split()
        ips.extend(out)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        out = subprocess.check_output(["tailscale", "ip", "-4"], text=True).strip()
        if out and out not in ips:
            ips.insert(0, out)
    except (OSError, subprocess.SubprocessError):
        pass
    # unique, prefer tailscale-looking 100.x first already inserted
    seen = set()
    ordered = []
    for ip in ips:
        if ip not in seen and ":" not in ip:
            seen.add(ip)
            ordered.append(ip)
    return ordered or ["127.0.0.1"]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Serve newest Argus clip over HTTP")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args(argv)

    clips = sorted(
        cfg.CLIPS_DIR.glob("*.mp4"),
        key=lambda x: x.stat().st_mtime,
        reverse=True,
    )
    clips = [c for c in clips if c.stat().st_size > 1000]
    if not clips:
        print(f"No MP4 clips in {cfg.CLIPS_DIR}", file=sys.stderr)
        return 1

    latest = clips[0]
    name = latest.name
    cfg.CLIPS_DIR.mkdir(parents=True, exist_ok=True)

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(cfg.CLIPS_DIR), **k)

        def log_message(self, fmt, *args):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # Prefer Tailscale IP in the printed URL
    ips = _lan_ips()
    host = next((ip for ip in ips if ip.startswith("100.")), ips[0])

    httpd = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"Serving {cfg.CLIPS_DIR}")
    print(f"Latest: {latest} ({latest.stat().st_size} bytes)")
    print()
    print(f"Open on your PC:")
    print(f"  http://{host}:{args.port}/{name}")
    print(f"  http://{host}:{args.port}/")
    for ip in ips:
        if ip != host:
            print(f"  (also) http://{ip}:{args.port}/{name}")
    print("\nCtrl+C to stop the server.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
