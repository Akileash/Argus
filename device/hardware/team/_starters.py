"""Starter ``mapping.yaml`` texts written by ``argus-cli team init <module>`` (spec §5.5).

Each is valid as is: with no ``command`` / ``python`` set the module stays "not pasted yet" and the supervisor
falls back. Fill in the field names from your real output, then run ``argus-cli team check <module>``.
"""

from __future__ import annotations

_RESTART = """
# restart: { initial_s: 0.5, max_s: 10 }   # a crashed script is restarted with this backoff
# warmup_s: 0                              # ignore the first N seconds after (re)start
# ignore: '^(Starting|Ready)'              # lines matching this regex are skipped silently
"""

_SOURCE = """# --- how to run your code: pick ONE ---
# command: [python, vendor/{script}.py]    # runs your script; relative paths start in this folder
# python: vendor.{script}                  # or import it ...
# call: read_once                          # ... and call this function in a loop (interval_s: 0.01)
"""

_FORMAT = {
    "json": "format: json                  # one JSON object per line (an array or a dict per reading also works)\n",
    "text": "format: text                  # e.g. 'R2=148.0 R3=151.2'; or add  regex: '(?P<id>R\\d)=(?P<v>[\\d.]+)'\n"
            "                              # (one reading per match) or  columns: [a, b, c]  + delimiter: ','\n",
}

_BODY = {
    "imu": """fields:                       # names in YOUR output (dotted paths: accel.x, data.0)
  ax: ax
  ay: ay
  az: az
  gx: gx
  gy: gy
  gz: gz
accel_unit: g                 # g | m/s2 | mg | lsb:16384 (raw counts per g) | a number (multiplier)
gyro_unit: deg/s              # deg/s | rad/s | mdps | lsb:131 (raw counts per deg/s)
# axes: { x: x, y: y, z: z }  # hat frame is x forward / y left / z up; e.g. { x: -y, y: x, z: z }
""",
    "range": """unit: mm                      # mm | cm | m | in
no_echo: [0, -1]              # raw values that mean "no echo" (null / nan / timeout always do)
# Pick ONE layout:
sensors: { R2: R2, R3: R3 }   #   wide: one line carries every sensor; R2 is the field named "R2" in your output
# id: sensor                  #   long: one record per sensor; the field holding its name ...
# value: distance             #   ... and the field holding the reading
# ids: { back_left: R2, back_right: R3 }   # their sensor names -> R2 / R3
""",
    "hat_on": """fields:
  range: distance             # the field holding the distance
  # status: status            # optional: a status field; readings whose status is not in ok_values become "error"
unit: mm                      # mm | cm | m
no_echo: [-1]                 # raw values meaning "nothing in range" (the VL53L0X also reads ~8190: see max_mm)
max_mm: 2000                  # at or above this the reading counts as out_of_range
# ok_values: [0, ok]
""",
    "env": """fields:
  co2_ppm: co2
  temp_c: temp
  rh_pct: rh
temp_unit: c                  # c | f | k
rh_unit: pct                  # pct | fraction
""",
}

_OTHER = {
    "haptics": """# Pick ONE way to drive the motors:
# python: vendor.motors       # import it; call(motor, value) is  call: set_duty
# call: set_duty
# motors: { top: 18, back: 19 }   # contract name -> what your function expects as first argument
# scale: 100                      # intensity 0..1 is multiplied by this (0..100 duty)
# command: [python, vendor/motor_daemon.py]   # or write lines to a script's stdin ...
# port: /dev/ttyACM0                          # ... or to the Arduino serial port (needs pyserial)
# baud: 115200
# line: "H,{motor},{pwm}\\n"   # {motor} {level} (0..1) {pwm} (0..255) {pct} (0..100)
""",
    "camera": """# Camera is kept minimal: your script saves the last N seconds to a file.
# command: [python, vendor/capture.py]    # optional: long-running capture, started with the module
# save_command: [python, vendor/save_clip.py, --seconds, "{seconds}", --out, "{path}"]
#   placeholders: {path} {seconds} {end_ago_s} {start_ns} {end_ns}
# save_timeout_s: 30
""",
}


def starter(module: str, fmt: str = "json", script: str = "their_script") -> str:
    head = f"# Team {module} mapping (spec §5.5). Check it with: argus-cli team check {module} --sample sample.txt\n"
    if module in _OTHER:
        return head + _OTHER[module]
    return head + _SOURCE.format(script=script) + _FORMAT[fmt] + _RESTART + "\n" + _BODY[module]
