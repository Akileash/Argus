"""L2 detector contract (PM-owned; spec §4, §5.1).

Detectors are PURE and deterministic: no threads, no I/O, no wall clock.
The runtime feeds them samples in timestamp order and collects Detections.
That makes them testable with synthetic streams and replayable from recordings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from argus.contracts import EnvSample, ImuSample, RangeSample, SensorId, TofSample

DetectionType = Literal[
    "struck_by_warning",   # live warning while object approaches (drives haptics, repeats)
    "struck_by_near_miss",  # confirmed event (cut a clip)
    "struck_by_contact",    # near miss upgraded by an impact within 1.5 s (spec §4.1)
    "head_impact",
    "fall",
    "hat_knocked_off",
    "hat_removed",
    "hat_on",
]


@dataclass(frozen=True, slots=True)
class Detection:
    t_ns: int
    type: DetectionType
    sensor_id: SensorId | None = None
    # Metrics mirror contracts/event.schema.json -> metrics (null when n/a).
    peak_g: float | None = None
    peak_g_saturated: bool = False
    closing_rate_mps: float | None = None
    ttc_s: float | None = None
    range_m: float | None = None
    free_fall_ms: float | None = None
    drop_height_m: float | None = None
    # Free-form intermediate values for tuning logs / trace "detections".
    debug: dict = field(default_factory=dict)


@dataclass(slots=True)
class DetectorState:
    """Shared read-only context the runtime passes to every detector each step."""

    hat_on: bool = True


class Detector(Protocol):
    """Each detector consumes the streams it cares about and ignores the rest.

    Constructed with its config section: `Detector(cfg: dict)` where cfg is
    config.yaml -> thresholds.<detector_name>. All thresholds come from cfg
    (defaults allowed, but they must match the spec numbers).
    """

    def on_imu(self, s: ImuSample, state: DetectorState) -> list[Detection]: ...
    def on_range(self, s: RangeSample, state: DetectorState) -> list[Detection]: ...
    def on_tof(self, s: TofSample, state: DetectorState) -> list[Detection]: ...
    def on_env(self, s: EnvSample, state: DetectorState) -> list[Detection]: ...
