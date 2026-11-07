"""The declarative mapping layer (spec §5.5): units, axes, ids, sentinels, formats, bad lines. No processes."""

from __future__ import annotations

import math

import pytest

from argus.contracts import EnvSample, ImuSample, RangeSample, TofSample
from hardware.team._mapping import LineError, Mapper, MappingError, load_mapping, unknown_keys

IMU = {"fields": {k: k for k in ("ax", "ay", "az", "gx", "gy", "gz")}}


def imu(**kw):
    return Mapper("imu", {**IMU, **kw})


def test_imu_json_units_and_axes():
    m = imu(accel_unit="m/s2", gyro_unit="rad/s", axes={"x": "-y", "y": "x", "z": "z"})
    line = '{"ax": 0.0, "ay": -9.80665, "az": 9.80665, "gx": 0, "gy": 0, "gz": 1.0}'
    (s,) = m.parse(line, 123)
    assert s == ImuSample(123, 1.0, 0.0, 1.0, 0.0, 0.0, pytest.approx(180 / math.pi))
    # their y = -1 g  ->  hat x = -(-1) = +1 g (axis remap with sign)


@pytest.mark.parametrize("unit,raw,g", [("g", 1.0, 1.0), ("mg", 1000, 1.0), ("lsb:16384", 16384, 1.0), (2.0, 0.5, 1.0)])
def test_accel_units(unit, raw, g):
    (s,) = imu(accel_unit=unit).parse(f'{{"ax":{raw},"ay":0,"az":0,"gx":0,"gy":0,"gz":0}}', 0)
    assert s.ax == pytest.approx(g)


def test_gyro_lsb_scale():
    (s,) = imu(gyro_unit="lsb:131").parse('{"ax":0,"ay":0,"az":1,"gx":131,"gy":0,"gz":-262}', 0)
    assert (s.gx, s.gz) == (pytest.approx(1.0), pytest.approx(-2.0))


def test_imu_dotted_paths_arrays_and_json_array_of_numbers():
    m = Mapper("imu", {"fields": {"ax": "a.0", "ay": "a.1", "az": "a.2", "gx": "g.x", "gy": "g.y", "gz": "g.z"}})
    (s,) = m.parse('{"a": [0.1, 0.2, 0.9], "g": {"x": 1, "y": 2, "z": 3}, "t": 99}', 5)
    assert (s.ax, s.az, s.gz, s.t_ns) == (0.1, 0.9, 3.0, 5)  # their "t" is ignored, arrival time is used
    arr = Mapper("imu", {"fields": dict(zip(("ax", "ay", "az", "gx", "gy", "gz"), "012345", strict=True))})
    assert arr.parse("[0, 0, 1, 4, 5, 6]", 0)[0].gz == 6.0


def test_imu_text_default_pairs_and_columns():
    (s,) = Mapper("imu", {"format": "text", **IMU}).parse("ax=0.01 ay=-0.00 az=1.01 gx=0.4 gy=-0.3 gz=0.2", 1)
    assert (s.ax, s.gy) == (0.01, -0.3)
    cols = Mapper("imu", {"format": "text", "delimiter": ",", "columns": list(IMU["fields"]), **IMU})
    assert cols.parse("0.01, 0.02, 1.0, 1, 2, 3", 0)[0].gz == 3.0
    with pytest.raises(LineError, match="expected 6 columns"):
        cols.parse("1,2,3", 0)


def test_range_long_layout_ids_cm_and_sentinels():
    m = Mapper("range", {"id": "sensor", "value": "d", "ids": {"back_left": "R2", "back_right": "R3"},
                         "unit": "cm", "no_echo": [0, -1]})
    assert m.parse('{"sensor": "back_left", "d": 14.8}', 1) == [RangeSample(1, "R2", pytest.approx(148.0))]
    assert m.parse('{"sensor": "back_right", "d": 0}', 1)[0].range_mm is None
    assert m.parse('{"sensor": "back_right", "d": -1}', 1)[0].range_mm is None
    assert m.parse('{"sensor": "back_right", "d": null}', 1)[0].range_mm is None
    assert m.parse('{"sensor": "back_right", "d": "timeout"}', 1)[0].range_mm is None
    with pytest.raises(LineError, match="front_left"):
        m.parse('{"sensor": "front_left", "d": 5}', 1)


def test_range_wide_text_and_json_array_of_readings():
    m = Mapper("range", {"format": "text", "unit": "cm", "sensors": {"R2": "R2", "R3": "R3"}})
    a, b = m.parse("R2=148.0 R3=0", 7)
    assert (a.sensor_id, a.range_mm, b.sensor_id, b.range_mm) == ("R2", pytest.approx(1480.0), "R3", None)
    assert [s.sensor_id for s in m.parse("R3=10", 0)] == ["R3"]  # a sensor missing from the line is skipped
    arr = Mapper("range", {"id": "n", "value": "v", "ids": {"2": "R2", "3": "R3"}, "unit": "m", "no_echo": [0]})
    got = arr.parse('[{"n": 2, "v": 1.5}, {"n": 3, "v": 0}]', 0)
    assert [(s.sensor_id, s.range_mm) for s in got] == [("R2", 1500.0), ("R3", None)]


def test_range_regex_one_record_per_match_and_valid_window():
    m = Mapper("range", {"format": "text", "regex": r"(?P<id>R\d)\s*:\s*(?P<v>[\d.]+)\s*cm", "id": "id",
                         "value": "v", "unit": "cm", "valid_mm": [20, 4500]})
    got = m.parse("R2 : 12.5 cm | R3 : 900 cm", 0)
    assert [(s.sensor_id, s.range_mm) for s in got] == [("R2", 125.0), ("R3", None)]  # 9000 mm is out of window


def test_hat_on_metres_and_vl53_out_of_range_and_status():
    m = Mapper("hat_on", {"fields": {"range": "d", "status": "st"}, "unit": "m"})
    assert m.parse('{"d": 0.063, "st": 0}', 1) == [TofSample(1, pytest.approx(63.0), "ok")]
    assert m.parse('{"d": 8.19, "st": 0}', 1) == [TofSample(1, None, "out_of_range")]
    assert m.parse('{"d": 0.063, "st": 4}', 1) == [TofSample(1, None, "error")]


def test_env_text_units_and_banner():
    m = Mapper("env", {"format": "text", "fields": {"co2_ppm": "co2", "temp_c": "temp", "rh_pct": "rh"},
                       "temp_unit": "f", "rh_unit": "fraction"})
    (s,) = m.parse("SCD41 reading: co2=655 temp=71.6 rh=0.412", 3)
    assert s == EnvSample(3, 655.0, pytest.approx(22.0), pytest.approx(41.2))


@pytest.mark.parametrize("line,why", [
    ("", None), ("Starting sensor", "not JSON"), ('{"ax": 1', "bad JSON"), ('{"ax": 1}', "not found"),
    ('{"ax":"x","ay":0,"az":0,"gx":0,"gy":0,"gz":0}', "expected a number"), ("[1, 2]", "not found"),
])
def test_malformed_lines_raise_line_error_only(line, why):
    m = imu()
    if why is None:
        assert m.parse(line, 0) == []
    else:
        with pytest.raises(LineError, match=why):
            m.parse(line, 0)


def test_ignore_regex_skips_banner_lines_silently():
    m = imu(ignore="^(Starting|Ready)")
    assert m.parse("Starting sensor...", 0) == []


@pytest.mark.parametrize("module,mapping,msg", [
    ("imu", {"fields": {"ax": "a"}}, "missing `ay`"),
    ("imu", {**IMU, "accel_unit": "furlongs"}, "unknown unit"),
    ("imu", {**IMU, "axes": {"x": "y", "y": "y", "z": "z"}}, "exactly once"),
    ("range", {"unit": "cm"}, "either `sensors"),
    ("range", {"sensors": {"R9": "x"}}, "R9"),
    ("imu", {**IMU, "format": "xml"}, "format"),
    ("env", {"fields": {}}, "at least one"),
    ("haptics", {}, "no streaming mapping"),
])
def test_bad_mappings_fail_at_creation_with_a_clear_message(module, mapping, msg):
    with pytest.raises(MappingError, match=msg):
        Mapper(module, mapping)


def test_mapping_file_and_options_merge(tmp_path):
    f = tmp_path / "m.yaml"
    f.write_text("unit: cm\nno_echo: [0]\nsensors: {R2: a}\n")
    got = load_mapping("range", tmp_path, {"mapping_file": str(f), "no_echo": [0, -1], "order": ["R2"]})
    assert got["unit"] == "cm" and got["no_echo"] == [0, -1] and got["sensors"] == {"R2": "a"}
    with pytest.raises(MappingError, match="does not exist"):
        load_mapping("range", tmp_path, {"mapping_file": str(tmp_path / "nope.yaml")})


def test_typo_detection():
    assert unknown_keys("range", {"unit": "cm", "order": ["R2"], "no_eco": [0]}) == [
        "unknown key 'no_eco' (did you mean 'no_echo'?)"]
