"""WearDetector timing and fault-hold tests."""

from __future__ import annotations

from argus_sensors.sensors.vl6180x import WearDetector, OFF_DWELL_MS


def test_boot_off():
    d = WearDetector()
    s = d.snapshot(0.0)
    assert s.helmet_worn is False
    assert s.state == "OFF"


def test_on_after_5s_near():
    d = WearDetector()
    t = 0.0
    d.update(mm=85, status=0, fault=False, now=t)
    s = d.update(mm=85, status=0, fault=False, now=t + 4.9)
    assert s.helmet_worn is False
    assert s.changed is False
    assert s.on_in_ms is not None and s.on_in_ms < 200
    s = d.update(mm=85, status=0, fault=False, now=t + 5.0)
    assert s.helmet_worn is True
    assert s.state == "ON"
    assert s.changed is True


def test_off_after_3s_far():
    d = WearDetector()
    t = 0.0
    d.update(mm=85, status=0, fault=False, now=t)
    d.update(mm=85, status=0, fault=False, now=t + 5.0)
    assert d.wear_on is True
    s = d.update(mm=None, status=7, fault=False, now=t + 5.1)
    assert s.helmet_worn is True
    s = d.update(mm=None, status=7, fault=False, now=t + 5.1 + 2.9)
    assert s.helmet_worn is True
    s = d.update(mm=None, status=7, fault=False, now=t + 5.1 + 3.0)
    assert s.helmet_worn is False
    assert s.changed is True


def test_fault_holds_state_and_cancels_near():
    d = WearDetector()
    t = 0.0
    d.update(mm=85, status=0, fault=False, now=t)
    d.update(mm=85, status=0, fault=False, now=t + 3.0)
    s = d.update(mm=None, status=255, fault=True, now=t + 3.1)
    assert s.helmet_worn is False
    assert s.status == 255
    assert s.sensor_ok is False
    assert d.near_timing is False
    d.update(mm=85, status=0, fault=False, now=t + 3.2)
    s = d.update(mm=85, status=0, fault=False, now=t + 3.2 + 4.9)
    assert s.helmet_worn is False
    s = d.update(mm=85, status=0, fault=False, now=t + 3.2 + 5.01)
    assert s.helmet_worn is True


def test_fault_while_on_does_not_flip():
    d = WearDetector()
    t = 0.0
    d.update(mm=85, status=0, fault=False, now=t)
    d.update(mm=85, status=0, fault=False, now=t + 5.0)
    assert d.wear_on is True
    s = d.update(mm=None, status=255, fault=True, now=t + 6.0)
    assert s.helmet_worn is True
    assert s.changed is False


def test_countdown_clamped_at_zero():
    d = WearDetector()
    t = 0.0
    d.update(mm=85, status=0, fault=False, now=t)
    d.update(mm=85, status=0, fault=False, now=t + 5.0)
    d.update(mm=None, status=7, fault=False, now=t + 5.1)
    d.far_timing = True
    d.far_start = t + 5.1
    s = d.snapshot(t + 5.1 + (OFF_DWELL_MS / 1000.0) + 10.0)
    assert s.off_in_ms == 0


def test_too_close_not_near():
    d = WearDetector()
    s = d.update(mm=40, status=0, fault=False, now=0.0)
    assert s.near is False


def test_too_far_not_near():
    d = WearDetector()
    # 255 is the VL6180X no-target sentinel → not a valid near reading
    s = d.update(mm=255, status=0, fault=False, now=0.0)
    assert s.near is False


def test_status_nonzero_invalid():
    d = WearDetector()
    s = d.update(mm=85, status=7, fault=False, now=0.0)
    assert s.near is False
    assert s.distance_mm is None
