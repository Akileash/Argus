#!/usr/bin/env python3
"""
Build a human-readable PDF safety report from events.jsonl.

This is for YOU to read / pitch with — NOT the backend transfer format.
Backend JSON stays in outbox/ via export_backend.py.

Usage:
  cd ~/argus && source .venv/bin/activate
  python make_report_pdf.py
  python make_report_pdf.py --out Argus_Safety_Report.pdf
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# Reuse the analyzer (same labels / narratives)
from analyze_events import DEFAULT_EVENTS, explain, load_events

ARGUS_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Minimal PDF writer (stdlib only — no pip install needed)
# ---------------------------------------------------------------------------

class SimplePDF:
    """Very small multi-page text PDF (Helvetica, 612×792 letter)."""

    def __init__(self, title: str = "Argus Report"):
        self.title = title
        self.pages: List[List[tuple]] = [[]]  # list of (font_size, text) lines
        self._y_left = 720.0

    def _new_page(self) -> None:
        self.pages.append([])
        self._y_left = 720.0

    def add_line(self, text: str, size: int = 11, gap: float = 0.0) -> None:
        # crude wrap at ~90 chars for size 11
        width = max(20, int(90 * 11 / size))
        chunks = _wrap(text, width) or [""]
        line_h = size + 4 + gap
        for chunk in chunks:
            if self._y_left < 54:
                self._new_page()
            self.pages[-1].append((size, chunk))
            self._y_left -= line_h

    def add_blank(self, n: float = 10.0) -> None:
        if self._y_left < 54:
            self._new_page()
        self.pages[-1].append((1, ""))
        self._y_left -= n

    def save(self, path: Path) -> None:
        objects: List[bytes] = []

        def add_obj(data: bytes) -> int:
            objects.append(data)
            return len(objects)

        # Catalog / pages built after page objects
        font_id = add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
        bold_id = add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")

        page_ids: List[int] = []
        content_ids: List[int] = []

        for page_lines in self.pages:
            stream = _page_stream(page_lines, font_id, bold_id)
            content_ids.append(
                add_obj(
                    f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1")
                    + stream
                    + b"\nendstream"
                )
            )

        pages_kids = []
        for cid in content_ids:
            pid = add_obj(
                (
                    f"<< /Type /Page /Parent {{PAGES}} /MediaBox [0 0 612 792] "
                    f"/Resources << /Font << /F1 {font_id} 0 R /F2 {bold_id} 0 R >> >> "
                    f"/Contents {cid} 0 R >>"
                ).encode("latin-1")
            )
            page_ids.append(pid)
            pages_kids.append(f"{pid} 0 R")

        pages_id = add_obj(
            (
                f"<< /Type /Pages /Kids [{' '.join(pages_kids)}] "
                f"/Count {len(page_ids)} >>"
            ).encode("latin-1")
        )
        # patch parent refs
        for i, pid in enumerate(page_ids):
            objects[pid - 1] = objects[pid - 1].replace(b"{PAGES}", f"{pages_id} 0 R".encode())

        cat_id = add_obj(
            f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("latin-1")
        )
        info_id = add_obj(
            f"<< /Title ({_pdf_escape(self.title)}) /Producer (Argus make_report_pdf) "
            f"/CreationDate (D:{datetime.now().strftime('%Y%m%d%H%M%S')}) >>".encode("latin-1")
        )

        # Write file
        out = bytearray(b"%PDF-1.4\n")
        offsets = [0]
        for i, obj in enumerate(objects, 1):
            offsets.append(len(out))
            out.extend(f"{i} 0 obj\n".encode("latin-1"))
            out.extend(obj)
            out.extend(b"\nendobj\n")
        xref_pos = len(out)
        out.extend(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
        out.extend(b"0000000000 65535 f \n")
        for off in offsets[1:]:
            out.extend(f"{off:010d} 00000 n \n".encode("latin-1"))
        out.extend(
            (
                f"trailer\n<< /Size {len(objects) + 1} /Root {cat_id} 0 R "
                f"/Info {info_id} 0 R >>\n"
                f"startxref\n{xref_pos}\n%%EOF\n"
            ).encode("latin-1")
        )
        path.write_bytes(out)


def _wrap(text: str, width: int) -> List[str]:
    text = text.replace("\t", " ")
    words = text.split(" ")
    lines: List[str] = []
    cur = ""
    for w in words:
        trial = w if not cur else cur + " " + w
        if len(trial) <= width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            # hard-break very long tokens
            while len(w) > width:
                lines.append(w[:width])
                w = w[width:]
            cur = w
    if cur or not lines:
        lines.append(cur)
    return lines


def _pdf_escape(s: str) -> str:
    return (
        s.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _to_latin1(s: str) -> str:
    # Helvetica core fonts are WinAnsi-ish; strip unsupported chars
    return s.encode("latin-1", errors="replace").decode("latin-1")


def _page_stream(lines: List[tuple], font_id: int, bold_id: int) -> bytes:
    y = 742
    parts = ["BT"]
    for size, text in lines:
        if text == "" and size == 1:
            y -= 10
            continue
        safe = _pdf_escape(_to_latin1(text))
        # first line of a block uses bold if it looks like a header
        font = "F2" if (size >= 14 or text.startswith("[") or text.startswith("ARGUS")
                         or text.startswith("SAFETY") or text.startswith("Event ")) else "F1"
        parts.append(f"/{font} {size} Tf")
        parts.append(f"1 0 0 1 54 {y} Tm")
        parts.append(f"({safe}) Tj")
        y -= size + 4
        if y < 50:
            break
    parts.append("ET")
    return "\n".join(parts).encode("latin-1")


# ---------------------------------------------------------------------------
# Report content
# ---------------------------------------------------------------------------

def build_pdf(events_path: Path, out_path: Path, last: int = 0) -> Path:
    events = load_events(events_path)
    if last > 0:
        events = events[-last:]
    analyses = [explain(e) for e in events]

    pdf = SimplePDF(title="Argus Safety Report (human readable)")
    pdf.add_line("ARGUS — Incident Readout", size=18)
    pdf.add_line("Human-readable report — NOT the backend transfer payload", size=10)
    pdf.add_line(f"Generated: {datetime.now().astimezone().isoformat(timespec='seconds')}", size=10)
    pdf.add_line(f"Source: {events_path}", size=10)
    pdf.add_line(f"Events: {len(analyses)}", size=10)
    pdf.add_blank(14)

    # Summary counts
    counts = {}
    for a in analyses:
        counts[a["label"]] = counts.get(a["label"], 0) + 1
    pdf.add_line("SAFETY ROLLUP", size=14)
    for k, v in sorted(counts.items()):
        pdf.add_line(f"  {k}: {v}", size=11)
    critical = [a for a in analyses if a["severity"] == "CRITICAL"]
    pdf.add_line(f"  CRITICAL count: {len(critical)}", size=11)
    pdf.add_blank(12)

    for i, a in enumerate(analyses, 1):
        pdf.add_line(f"Event {i}: {a['label']}", size=14)
        pdf.add_line(f"Severity: {a['severity']}   Priority: {a['priority']}   Confidence: {a['confidence']}", size=11)
        pdf.add_line(f"Time: {a['timestamp']}", size=10)
        pdf.add_line(f"ID: {a['event_id']}", size=9)
        pdf.add_line(
            f"Peak {a['peak_accel_g']:.1f} g | Gyro {a['peak_gyro_dps']:.0f} dps | "
            f"Freefall {a['freefall_ms']:.0f} ms | Tilt {a['tilt_change_deg']:.0f} deg | "
            f"Still={a['still_after']} | Score={a['score']:.2f}",
            size=10,
        )
        pdf.add_line(
            f"Worn before={a['worn_before']} after={a['worn_after']} | "
            f"Set-down flag={a['hat_possibly_set_down']}",
            size=10,
        )
        pdf.add_line("Why:", size=11)
        for r in a["reasons"]:
            pdf.add_line(f"  - {r}", size=10)
        pdf.add_line(f"Verdict: {a['verdict']}", size=11)
        pdf.add_line(f"Action: {a['recommended_action']}", size=10)
        for n in a["notes"]:
            pdf.add_line(f"Note: {n}", size=10)
        pdf.add_line(f"Story: {a['narrative']}", size=10)
        pdf.add_blank(12)

    pdf.add_line("END OF REPORT", size=12)
    pdf.add_line("Backend JSON for upload lives in ~/argus/outbox/ (export_backend.py).", size=9)
    pdf.save(out_path)
    return out_path


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Generate readable Argus PDF report")
    ap.add_argument("--events", default=str(DEFAULT_EVENTS))
    ap.add_argument("--out", default=str(ARGUS_DIR / "Argus_Safety_Report.pdf"))
    ap.add_argument("--last", type=int, default=0)
    args = ap.parse_args(argv)

    try:
        path = build_pdf(Path(args.events), Path(args.out), last=args.last)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(f"Wrote {path}")
    print("(Readable PDF only — not for backend data transfer.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
