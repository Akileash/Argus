"""Argus-written hardware drivers (impl `argus`). Each submodule exposes `create(ctx)`.

Hardware libraries (smbus2, lgpio, picamera2, adafruit-*) are imported lazily, never at import time.
"""
