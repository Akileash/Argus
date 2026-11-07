"""Tiny local control channel so ``argus-cli reload`` can talk to a running ``argus-cli run``.

Newline-delimited JSON over a Unix socket. Commands: ``status``, ``reload`` (module[, impl]) and
``fault`` (module, kind=kill|stall|revive: drills for mock impls). Not a network API.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import socketserver
import tempfile
import threading
from pathlib import Path
from typing import Any

from argus.supervisor import Supervisor

log = logging.getLogger(__name__)


def socket_path() -> str:
    # Kept short on purpose: AF_UNIX paths are limited to ~104 bytes on macOS.
    return os.environ.get("ARGUS_CONTROL_SOCK") or str(Path(tempfile.gettempdir()) / f"argus-{os.getuid()}.sock")


def handle(sup: Supervisor, req: dict[str, Any]) -> dict[str, Any]:
    cmd = req.get("cmd")
    try:
        if cmd == "status":
            return {"ok": True, "status": sup.status()}
        if cmd == "reload":
            dt = sup.reload(req["module"], req.get("impl"))
            return {"ok": True, "module": req["module"], "seconds": dt, "impl": sup.status()[req["module"]]["impl"]}
        if cmd == "fault":
            inst = sup.module(req["module"])
            getattr(inst, req["kind"])()  # kill | stall | revive (mock impls only)
            return {"ok": True}
        extra = getattr(sup, "control_handlers", {}).get(cmd)  # registered by argus.pipeline (e.g. "trigger")
        if extra is not None:
            return extra(sup, req)
        return {"ok": False, "error": f"unknown cmd {cmd!r}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


class ControlSocketBusy(RuntimeError):
    """A live instance already owns the control socket."""


class ControlServer:
    def __init__(self, sup: Supervisor, path: str | None = None) -> None:
        self.path = path or socket_path()
        outer = self

        class H(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                line = self.rfile.readline()
                try:
                    resp = handle(sup, json.loads(line))
                except Exception as e:  # noqa: BLE001
                    resp = {"ok": False, "error": f"bad request: {e}"}
                self.wfile.write((json.dumps(resp, default=str) + "\n").encode())

        if Path(outer.path).exists():
            try:
                live = send({"cmd": "status"}, outer.path, timeout=2.0)
            except (ConnectionError, OSError, ValueError):
                live = None  # stale socket file from a dead process
            if live is not None:
                raise ControlSocketBusy(
                    f"another 'argus-cli run' is already listening on {outer.path}; stop it first "
                    "(e.g. `sudo systemctl stop argus`) or run with --no-control")
            Path(outer.path).unlink(missing_ok=True)
        self._srv = socketserver.ThreadingUnixStreamServer(self.path, H)
        self._thread = threading.Thread(target=self._srv.serve_forever, name="argus-control", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()
        Path(self.path).unlink(missing_ok=True)


def send(req: dict[str, Any], path: str | None = None, timeout: float = 10.0) -> dict[str, Any]:
    """Client side. Raises ConnectionError when no ``argus-cli run`` is listening."""
    p = path or socket_path()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect(p)
        except (FileNotFoundError, ConnectionRefusedError):
            raise ConnectionError(f"no running 'argus-cli run' found (socket {p})") from None
        s.sendall((json.dumps(req) + "\n").encode())
        return json.loads(s.makefile().readline())
