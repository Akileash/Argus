"""FAKE teammate haptics script (tests only): reads command lines on stdin (``H,top,128``) and appends each to the
file named by $ARGUS_FAKE_HAPTICS_LOG, acknowledging with OK like the Arduino sketch (spec §2.4)."""
import os
import sys

log = os.environ.get("ARGUS_FAKE_HAPTICS_LOG", os.devnull)
for line in sys.stdin:
    with open(log, "a") as f:
        f.write(line)
    print("OK", flush=True)
