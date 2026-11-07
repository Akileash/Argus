"""Device pairing: write backend creds into config.yaml and verify them (contracts/api.md #2)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
from ruamel.yaml import YAML

from argus.config import DEFAULT_CONFIG_PATH


class PairingError(Exception):
    pass


def resolve_config_path(path: Path | str | None) -> Path:
    return Path(path or os.environ.get("ARGUS_CONFIG") or DEFAULT_CONFIG_PATH)


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
    try:
        with tmp.open("w") as f:
            y.dump(data, f)
        if path.exists():
            os.chmod(tmp, path.stat().st_mode & 0o777)
        os.replace(tmp, path)
    except PermissionError:  # directory not writable but the file is
        tmp.unlink(missing_ok=True)
        with path.open("w") as f:
            y.dump(data, f)
