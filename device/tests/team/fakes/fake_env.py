"""FAKE teammate env script (tests only): plain text with a banner, degrees F and a humidity fraction.
Prints immediately, then every ``--period`` seconds (default 5 s = 0.2 Hz). 71.6 F is 22.0 C."""
import argparse
import time

ap = argparse.ArgumentParser()
ap.add_argument("--period", type=float, default=5.0)
a = ap.parse_args()
t0, n = time.monotonic(), 0
while True:
    print("SCD41 reading: co2=655 temp=71.6 rh=0.412", flush=True)
    n += 1
    time.sleep(max(0.0, t0 + n * a.period - time.monotonic()))
