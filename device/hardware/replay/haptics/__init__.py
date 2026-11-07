"""Haptics has nothing to replay: use the recording mock driver."""

from hardware.mock.haptics import create

__all__ = ["create"]
