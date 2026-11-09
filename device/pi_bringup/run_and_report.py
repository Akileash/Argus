#!/usr/bin/env python3
"""
Run live IMU monitor until Ctrl+C, then build the readable PDF report
and refresh backend outbox JSON.

  cd ~/argus && source .venv/bin/activate
  python run_and_report.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ARGUS = Path(__file__).resolve().parent
PY = ARGUS / ".venv" / "bin" / "python"


def main() -> int:
    py = str(PY if PY.exists() else sys.executable)
    print("Starting monitor. Move / drop / tap the IMU.")
    print("Press Ctrl+C when you are done — a PDF report will be generated.\n")

    # Monitor until user hits Ctrl+C (exit 0 / 130 are both fine)
    try:
        rc = subprocess.call([py, "-u", str(ARGUS / "imu_fall.py"), "monitor"])
    except KeyboardInterrupt:
        rc = 130
        print("\nMonitor stopped.")

    print("\nBuilding readable PDF report...")
    pdf_rc = subprocess.call([py, str(ARGUS / "make_report_pdf.py")])
    print("\nExporting backend JSON to outbox/...")
    exp_rc = subprocess.call([py, str(ARGUS / "export_backend.py")])

    print("\nDone.")
    print(f"  PDF:    {ARGUS / 'Argus_Safety_Report.pdf'}")
    print(f"  Events: {ARGUS / 'events.jsonl'}")
    print(f"  Outbox: {ARGUS / 'outbox'}")
    return 0 if pdf_rc == 0 else pdf_rc


if __name__ == "__main__":
    sys.exit(main())
