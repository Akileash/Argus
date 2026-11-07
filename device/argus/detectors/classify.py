"""Deterministic device event class (spec §3.4; contracts/event.schema.json $defs.event_class)."""

from __future__ import annotations

from typing import Literal

EventClass = Literal["near_miss", "incident", "ppe_log", "system"]

_CLASS: dict[str, EventClass] = {
    "struck_by_near_miss": "near_miss",
    # A live warning with no impact within +/-2 s is a near miss (spec §3.4).
    "struck_by_warning": "near_miss",
    "struck_by_contact": "incident",
    "head_impact": "incident",
    "fall": "incident",
    "hat_knocked_off": "incident",
    "hat_removed": "ppe_log",
    # Not an event type in the schema; if logged it is PPE state, not an incident.
    "hat_on": "ppe_log",
    "module_fallback": "system",
}


def device_class(detection_type: str) -> EventClass:
    try:
        return _CLASS[detection_type]
    except KeyError:
        raise ValueError(f"unknown detection/event type: {detection_type!r}") from None
