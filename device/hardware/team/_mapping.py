"""Declarative mapping for team adapters: what a teammate's output line means (spec §5.5).

Pure functions, no threads, no hardware: ``Mapper.parse(payload)`` turns ONE reading from a teammate's
code (a JSON line, a text line, or a Python object) into contract samples (``argus/contracts.py``):
range in mm, accel in g, gyro in deg/s, ``None`` = no echo, hat frame x forward / y left / z up.
The timestamp is NOT read from the payload: the caller passes the arrival time from the shared clock.

A malformed reading raises :class:`LineError`; the adapter counts it and skips the line.
``argus-cli team check`` runs exactly this code on pasted output, so what it prints is what the hat does.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from argus.contracts import SENSOR_IDS, EnvSample, ImuSample, RangeSample, TofSample


class MappingError(ValueError):
    """The mapping itself is wrong (bad unit name, missing key). Raised when the adapter is created."""


class LineError(ValueError):
    """One reading could not be parsed. The adapter counts it in ``error_count`` and moves on."""


# ---------------------------------------------------------------- units: factor to the contract unit
ACCEL_TO_G = {"g": 1.0, "mg": 1e-3, "m/s2": 1 / 9.80665, "m/s^2": 1 / 9.80665, "ms2": 1 / 9.80665}
GYRO_TO_DPS = {"deg/s": 1.0, "dps": 1.0, "mdps": 1e-3, "rad/s": 180 / math.pi, "rps": 180 / math.pi}
LENGTH_TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4}
RH_TO_PCT = {"pct": 1.0, "%": 1.0, "fraction": 100.0}
NO_ECHO_WORDS = {"none", "null", "nan", "inf", "-inf", "timeout", "nan.0", ""}


def unit_factor(table: Mapping[str, float], unit: Any, what: str) -> float:
    """``"cm"`` -> 10.0; a number multiplies; ``"lsb:16384"`` divides (raw sensor counts per unit)."""
    if isinstance(unit, (int, float)) and not isinstance(unit, bool):
        return float(unit)
    u = str(unit).strip().lower()
    if u.startswith("lsb:"):
        try:
            return 1.0 / float(u[4:])
        except (ValueError, ZeroDivisionError):
            raise MappingError(f"{what}: bad scale {unit!r}; write e.g. 'lsb:16384' (counts per g)") from None
    if u in table:
        return table[u]
    raise MappingError(f"{what}: unknown unit {unit!r}; use one of {sorted(table)}, 'lsb:<counts per unit>' "
                       "or a number (multiplier)")


def to_temp_c(v: float, unit: str) -> float:
    u = unit.lower()
    if u in ("c", "celsius"):
        return v
    if u in ("f", "fahrenheit"):
        return (v - 32) * 5 / 9
    if u in ("k", "kelvin"):
        return v - 273.15
    raise MappingError(f"temp_unit: unknown {unit!r}; use c, f or k")


# ---------------------------------------------------------------- reading -> records
def get_path(rec: Any, path: Any) -> Any:
    """Dotted path into dicts and lists: ``accel.x``, ``data.0``, ``R2``. Raises LineError if absent."""
    cur = rec
    for part in str(path).split("."):
        if isinstance(cur, Mapping) and part in cur:
            cur = cur[part]
        elif isinstance(cur, (list, tuple)) and part.lstrip("-").isdigit() and -len(cur) <= int(part) < len(cur):
            cur = cur[int(part)]
        else:
            raise LineError(f"field {str(path)!r} not found in {_short(rec)}")
    return cur


def _short(x: Any, n: int = 90) -> str:
    s = x if isinstance(x, str) else json.dumps(x, default=str)
    return s if len(s) <= n else s[:n] + "..."


def as_float(v: Any, what: str) -> float:
    if isinstance(v, bool) or v is None:
        raise LineError(f"{what}: expected a number, got {v!r}")
    try:
        f = float(v.strip() if isinstance(v, str) else v)
    except (TypeError, ValueError):
        raise LineError(f"{what}: expected a number, got {v!r}") from None
    if math.isnan(f) or math.isinf(f):
        raise LineError(f"{what}: not a finite number ({v!r})")
    return f


_NUM = r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
# `key=value` (value may be a word like `timeout`) or `key: number`; so "reading: co2=655" gives co2 only.
KV = re.compile(rf"([A-Za-z_][\w./-]*)\s*(?:=\s*({_NUM}|[A-Za-z_]\w*)|:\s*({_NUM}))")


class Mapper:
    """Parses readings of one module according to its (already merged) mapping dict."""

    def __init__(self, module: str, mapping: Mapping[str, Any]) -> None:
        self.module, self.m = module, dict(mapping)
        self.format = str(self.m.get("format", "json")).lower()
        if self.format not in ("json", "text"):
            raise MappingError(f"format must be 'json' or 'text', not {self.format!r}")
        self.ignore = re.compile(self.m["ignore"]) if self.m.get("ignore") else None
        try:
            self.regex = re.compile(self.m["regex"]) if self.m.get("regex") else None
        except re.error as e:
            raise MappingError(f"regex: {e}") from None
        build = getattr(self, f"_build_{module}", None)
        if build is None:
            raise MappingError(f"{module} has no streaming mapping (only imu, range, hat_on, env)")
        self._build = build
        self._prepare()

    # ---- text/json -> list of records (dict or list)
    def records(self, payload: Any) -> list[Any]:
        if isinstance(payload, str):
            line = payload.strip()
            if not line or (self.ignore and self.ignore.search(line)):
                return []
            return self._json(line) if self.format == "json" else self._text(line)
        if isinstance(payload, (dict, list, tuple)):
            return self._shape(payload)
        raise LineError(f"unsupported reading type {type(payload).__name__}")

    @staticmethod
    def _shape(obj: Any) -> list[Any]:
        if isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj):
            return list(obj)  # a JSON array of readings
        return [obj]  # a dict, or an array of numbers addressed by index

    def _json(self, line: str) -> list[Any]:
        if line[0] not in "{[":
            raise LineError(f"not JSON: {_short(line)}")
        try:
            return self._shape(json.loads(line))
        except json.JSONDecodeError as e:
            raise LineError(f"bad JSON ({e.msg}): {_short(line)}") from None

    def _text(self, line: str) -> list[Any]:
        if self.regex is not None:  # one record per match; named groups become the field names
            found = [mt.groupdict() or dict(enumerate(mt.groups())) for mt in self.regex.finditer(line)]
            if not found:
                raise LineError(f"regex did not match: {_short(line)}")
            return found
        if self.m.get("columns"):  # delimiter-separated values in a fixed order
            parts = [p.strip() for p in (line.split(self.m["delimiter"]) if self.m.get("delimiter") else line.split())]
            cols = list(self.m["columns"])
            if len(parts) != len(cols):
                raise LineError(f"expected {len(cols)} columns {cols}, got {len(parts)}: {_short(line)}")
            return [dict(zip(cols, parts, strict=True))]
        pairs = {k: eq or colon for k, eq, colon in KV.findall(line)}  # default: key=value pairs, e.g. "R2=148.0 R3=0"
        if not pairs:
            raise LineError(f"no key=value pairs found: {_short(line)}")
        return [pairs]

    # ---- records -> samples
    def parse(self, payload: Any, t_ns: int) -> list[Any]:
        out: list[Any] = []
        for rec in self.records(payload):
            out.extend(self._build(rec, t_ns))
        return out

    def _prepare(self) -> None:
        m = self.m
        if self.module == "imu":
            self.fields = {k: self._need("fields", k) for k in ("ax", "ay", "az", "gx", "gy", "gz")}
            self.accel_k = unit_factor(ACCEL_TO_G, m.get("accel_unit", "g"), "accel_unit")
            self.gyro_k = unit_factor(GYRO_TO_DPS, m.get("gyro_unit", "deg/s"), "gyro_unit")
            self.axes = _parse_axes(m.get("axes"))
        elif self.module == "range":
            self.len_k = unit_factor(LENGTH_TO_MM, m.get("unit", "mm"), "unit")
            self.no_echo = _sentinels(m.get("no_echo", [0, -1]))
            self.ids = {str(k): str(v) for k, v in (m.get("ids") or {}).items()}
            self.valid = m.get("valid_mm")
            if m.get("sensors"):
                self.layout = "wide"
            elif m.get("id") is not None and m.get("value") is not None:
                self.layout = "long"
            else:
                raise MappingError("range needs either `sensors: {R2: <field>, R3: <field>}` (one record "
                                   "carries every sensor) or both `id:` and `value:` (one record per sensor)")
            for sid in list(self.ids.values()) + list(m.get("sensors") or {}):
                if sid not in SENSOR_IDS:
                    raise MappingError(f"sensor id {sid!r} is not one of {list(SENSOR_IDS)}")
        elif self.module == "hat_on":
            self.range_path = self._need("fields", "range")
            self.len_k = unit_factor(LENGTH_TO_MM, m.get("unit", "mm"), "unit")
            self.no_echo = _sentinels(m.get("no_echo", [-1]))
            self.max_mm = float(m.get("max_mm", 2000))
        elif self.module == "env":
            f = m.get("fields") or {}
            self.fields = {k: f.get(k) for k in ("co2_ppm", "temp_c", "rh_pct")}
            if all(v is None for v in self.fields.values()):
                raise MappingError("env: `fields:` needs at least one of co2_ppm, temp_c, rh_pct")
            self.temp_unit = str(m.get("temp_unit", "c"))
            to_temp_c(0.0, self.temp_unit)
            self.rh_k = unit_factor(RH_TO_PCT, m.get("rh_unit", "pct"), "rh_unit")

    def _need(self, section: str, key: str) -> Any:
        v = (self.m.get(section) or {}).get(key)
        if v is None:
            raise MappingError(f"{self.module}: `{section}:` is missing `{key}` (the name of that field in your output)")
        return v

    def _build_imu(self, rec: Any, t_ns: int) -> list[Any]:
        v = {k: as_float(get_path(rec, p), k) for k, p in self.fields.items()}
        a, g = [v["ax"], v["ay"], v["az"]], [v["gx"], v["gy"], v["gz"]]
        a = [x * self.accel_k for x in a]
        g = [x * self.gyro_k for x in g]
        if self.axes:
            a, g = _remap(a, self.axes), _remap(g, self.axes)
        return [ImuSample(t_ns, *a, *g)]

    def _range_mm(self, raw: Any, what: str) -> float | None:
        if raw is None or (isinstance(raw, str) and raw.strip().lower() in NO_ECHO_WORDS):
            return None
        try:
            f = as_float(raw, what)
        except LineError:
            if isinstance(raw, float):  # NaN/inf from a library: a missed echo
                return None
            raise
        if f in self.no_echo:
            return None
        mm = f * self.len_k
        if self.valid and not (self.valid[0] <= mm <= self.valid[1]):
            return None
        return mm

    def _build_range(self, rec: Any, t_ns: int) -> list[Any]:
        if self.layout == "wide":
            out = []
            for sid, path in self.m["sensors"].items():  # a sensor absent from this line is skipped, not an error
                try:
                    raw = get_path(rec, path)
                except LineError:
                    continue
                out.append(RangeSample(t_ns, sid, self._range_mm(raw, sid)))
            if not out:
                raise LineError(f"none of the sensor fields {list(self.m['sensors'].values())} found in {_short(rec)}")
            return out
        raw_id = str(get_path(rec, self.m["id"]))
        sid = self.ids.get(raw_id, raw_id)
        if sid not in SENSOR_IDS:
            raise LineError(f"sensor {raw_id!r} is not mapped to R1..R4/TOP; add it under `ids:`")
        return [RangeSample(t_ns, sid, self._range_mm(get_path(rec, self.m["value"]), sid))]

    def _build_hat_on(self, rec: Any, t_ns: int) -> list[Any]:
        status_path = (self.m.get("fields") or {}).get("status")
        if status_path is not None:
            ok_values = {str(x) for x in self.m.get("ok_values", [0, "ok"])}
            if str(get_path(rec, status_path)) not in ok_values:
                return [TofSample(t_ns, None, "error")]
        mm = self._range_mm_tof(get_path(rec, self.range_path))
        return [TofSample(t_ns, mm, "ok" if mm is not None else "out_of_range")]

    def _range_mm_tof(self, raw: Any) -> float | None:
        if raw is None:
            return None
        f = as_float(raw, "range")
        mm = f * self.len_k
        return None if f in self.no_echo or mm <= 0 or mm >= self.max_mm else mm

    def _build_env(self, rec: Any, t_ns: int) -> list[Any]:
        def val(key: str) -> float | None:
            p = self.fields[key]
            return None if p is None else as_float(get_path(rec, p), key)

        co2, t, rh = val("co2_ppm"), val("temp_c"), val("rh_pct")
        return [EnvSample(t_ns, co2, None if t is None else to_temp_c(t, self.temp_unit),
                          None if rh is None else rh * self.rh_k)]


def _sentinels(values: Any) -> set[float]:
    return {float(v) for v in (values or []) if v is not None}  # null is always "no echo" already


def _parse_axes(spec: Any) -> list[tuple[int, float]] | None:
    """``{x: "-y", y: "x", z: "z"}`` -> for hat x/y/z: (index of their axis, sign)."""
    if not spec:
        return None
    out = []
    for hat in ("x", "y", "z"):
        s = str((spec or {}).get(hat, hat)).strip().lower().replace(" ", "")
        sign = -1.0 if s.startswith("-") else 1.0
        axis = s.lstrip("+-")
        if axis not in ("x", "y", "z"):
            raise MappingError(f"axes.{hat}: {spec.get(hat)!r} must be x, y or z, optionally with a - sign")
        out.append(("xyz".index(axis), sign))
    if len({i for i, _ in out}) != 3:
        raise MappingError(f"axes: each of their x, y, z must be used exactly once, got {spec}")
    return out


def _remap(v: list[float], axes: list[tuple[int, float]]) -> list[float]:
    return [sign * v[i] for i, sign in axes]


# ---------------------------------------------------------------- loading + merging
SOURCE_KEYS = ("command", "python")
COMMON_KEYS = {"format", "command", "python", "call", "setup", "interval_s", "warmup_s", "ignore", "regex",
               "columns", "delimiter", "restart", "mapping_file"}
KNOWN_KEYS = {
    "imu": COMMON_KEYS | {"fields", "accel_unit", "gyro_unit", "axes"},
    "range": COMMON_KEYS | {"unit", "no_echo", "id", "value", "ids", "sensors", "valid_mm"},
    "hat_on": COMMON_KEYS | {"fields", "unit", "no_echo", "max_mm", "ok_values"},
    "env": COMMON_KEYS | {"fields", "temp_unit", "rh_unit"},
    "haptics": {"python", "call", "setup", "teardown", "command", "port", "baud", "line", "motors", "scale",
                "mapping_file"},
    "camera": {"command", "save_command", "save_timeout_s", "mapping_file"},
}
# Options other impls read from the same `modules.<name>.options`; not typos.
FOREIGN_KEYS = {"order", "slot_ms", "ring_s", "ring_seconds", "cache_dir", "file", "speed", "loop", "scenario"}


def load_mapping(module: str, adapter_dir: Path, options: Mapping[str, Any] | None) -> dict[str, Any]:
    """``mapping.yaml`` beside the adapter (or ``options.mapping_file``), with ``options`` deep-merged over it."""
    from argus.config import deep_merge

    options = dict(options or {})
    base: dict[str, Any] = {}
    mf = options.get("mapping_file")
    path = Path(mf) if mf else adapter_dir / "mapping.yaml"
    if not mf and os.environ.get("ARGUS_TEAM_IGNORE_MAPPINGS"):
        path = Path(os.devnull)  # set by the unit-test run: a teammate's mapping.yaml must not change those tests
    if path.is_file():
        try:
            base = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError as e:
            raise MappingError(f"{path} is not valid YAML: {e}") from None
        if not isinstance(base, dict):
            raise MappingError(f"{path} must be a YAML mapping")
    elif mf:
        raise MappingError(f"mapping_file {mf} does not exist")
    return deep_merge(base, {k: v for k, v in options.items() if k != "mapping_file"})


def unknown_keys(module: str, mapping: Mapping[str, Any]) -> list[str]:
    """Human-readable warnings for keys nobody reads (typos), with a did-you-mean."""
    import difflib

    known = KNOWN_KEYS[module]
    return [f"unknown key {k!r}" + (f" (did you mean {s[0]!r}?)" if (s := difflib.get_close_matches(k, known, 1)) else "")
            for k in mapping if k not in known and k not in FOREIGN_KEYS]
