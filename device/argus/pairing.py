"""Device pairing: write backend creds into config.yaml and verify them (contracts/api.md #2)."""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from ruamel.yaml import YAML

from argus.config import LOCAL_CONFIG_PATH, local_config_path


class PairingError(Exception):
    pass


def resolve_config_path(path: Path | str | None) -> Path:
    """Where ``pair`` writes: ``--config``, else ``$ARGUS_CONFIG``, else the gitignored ``config.local.yaml``
    (a device token must never land in the committed ``config.yaml``)."""
    return Path(path or os.environ.get("ARGUS_CONFIG") or local_config_path() or LOCAL_CONFIG_PATH)


def fetch_me(base_url: str, token: str, *, timeout_s: float = 15.0,
             transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/api/devices/me"
    try:
        with httpx.Client(transport=transport, timeout=timeout_s) as c:
            r = c.get(url, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as e:
        raise PairingError(f"cannot reach {url}: {type(e).__name__}: {e}") from None
    if r.status_code == 401:
        raise PairingError("backend rejected the device token (401). Re-register the device in the dashboard.")
    if r.status_code != 200:
        raise PairingError(f"GET {url} -> HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


CLOCK_WARN_S = 60.0
CLOCK_FIX = ("Event timestamps (t0) come from this device's clock and the Pi has no battery clock. Fix it: "
             "`timedatectl status`, then `sudo timedatectl set-ntp true` (needs NTP reachable; venue Wi-Fi may block it) "
             "or set it by hand: `sudo timedatectl set-time 'YYYY-MM-DD HH:MM:SS'` (UTC).")


def clock_offset_s(me: dict[str, Any], now: float | None = None) -> float | None:
    """Device clock minus backend ``server_time`` in seconds (positive = device ahead); None if unparseable."""
    raw = me.get("server_time")
    if not isinstance(raw, str):
        return None
    try:
        server = datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return None
    return (time.time() if now is None else now) - server


def clock_warning(me: dict[str, Any], now: float | None = None) -> str | None:
    off = clock_offset_s(me, now)
    if off is None or abs(off) <= CLOCK_WARN_S:
        return None
    side = "ahead of" if off > 0 else "behind"
    return f"WARNING: device clock is {abs(off):.0f} s {side} the backend. {CLOCK_FIX}"


def describe(me: dict[str, Any]) -> str:
    w = me.get("assigned_worker")
    who = w["full_name"] if w else "(no worker assigned)"
    return f"device {me['serial']} (org {me['org_id']}), assigned worker: {who}"


def write_pairing(path: Path, base_url: str, token: str, serial: str | None) -> None:
    """Round-trip edit so comments and other keys survive."""
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    data = y.load(path.read_text()) if path.exists() else {}
    if data is None:
        data = {}
    if data.get("backend") is None:
        data["backend"] = {}
    data["backend"]["base_url"] = base_url.rstrip("/")
    data["backend"]["device_token"] = token
    if serial is not None:
        if data.get("device") is None:
            data["device"] = {}
        data["device"]["serial"] = serial
    tmp = path.with_name(path.name + ".tmp")
    # The file holds the device token: new files are 0600; an existing file keeps its mode (never loosened).
    mode = (path.stat().st_mode & 0o777) if path.exists() else 0o600
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            y.dump(data, f)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except PermissionError:  # directory not writable but the file is
        tmp.unlink(missing_ok=True)
        if not path.exists():
            raise
        with path.open("w") as f:
            y.dump(data, f)
