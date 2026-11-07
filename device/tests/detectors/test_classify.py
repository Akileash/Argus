import json
from pathlib import Path

import pytest

from argus.detectors.classify import device_class


@pytest.mark.parametrize("t,c", [
    ("struck_by_near_miss", "near_miss"), ("struck_by_warning", "near_miss"),
    ("struck_by_contact", "incident"), ("head_impact", "incident"), ("fall", "incident"),
    ("hat_knocked_off", "incident"), ("hat_removed", "ppe_log"), ("module_fallback", "system"),
])
def test_mapping(t, c):
    assert device_class(t) == c


def test_unknown_raises():
    with pytest.raises(ValueError):
        device_class("nope")


def test_matches_schema_enum():
    p = Path(__file__).resolve().parents[3] / "contracts" / "event.schema.json"
    defs = json.loads(p.read_text())["$defs"]
    for t in defs["event_type"]["enum"]:
        assert device_class(t) in defs["event_class"]["enum"]
