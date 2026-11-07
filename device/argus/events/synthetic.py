"""Synthetic detections for ``argus-cli trigger`` (stage backup, spec §7): plausible metrics, real clip."""

from __future__ import annotations

from argus.detectors.base import Detection

TRIGGER_TYPES = ("struck_by_near_miss", "struck_by_contact", "head_impact", "fall", "hat_knocked_off", "hat_removed")


def synthesize(event_type: str, t_ns: int) -> Detection:
    dbg = {"synthetic": True}
    if event_type in ("struck_by_near_miss", "struck_by_contact"):
        extra = {"peak_g": 9.4} if event_type == "struck_by_contact" else {}
        return Detection(t_ns, event_type, "R2", closing_rate_mps=2.6, ttc_s=0.7, range_m=1.8, debug=dbg, **extra)
    if event_type == "head_impact":
        return Detection(t_ns, "head_impact", peak_g=9.0, debug=dbg)
    if event_type == "fall":
        return Detection(t_ns, "fall", peak_g=12.0, free_fall_ms=450.0, drop_height_m=0.99, debug=dbg)
    if event_type in ("hat_knocked_off", "hat_removed"):
        return Detection(t_ns, event_type, debug={**dbg, "off_start_ns": t_ns})
    raise ValueError(f"cannot trigger {event_type!r}; choose from {', '.join(TRIGGER_TYPES)}")
