"""FAKE teammate hat_on script (tests only): JSON with METRES, 10 Hz. ``distance_m`` 0.063 is 63 mm on the head;
every 25th reading is the VL53L0X "nothing in range" value 8.19 m."""
import json
import time

t0, n = time.monotonic(), 0
while True:
    n += 1
    print(json.dumps({"distance_m": 8.19 if n % 25 == 0 else 0.063, "status": 0}), flush=True)
    time.sleep(max(0.0, t0 + n * 0.1 - time.monotonic()))
