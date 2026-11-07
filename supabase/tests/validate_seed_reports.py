"""Validate every seeded report_json / llm_draft_json against contracts/report.schema.json.

Run via supabase/tests/run.sh (which sets PSQL_CMD), or manually:
  PSQL_CMD="psql -d argus_test -At" uv run python ../supabase/tests/validate_seed_reports.py
"""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA = Path(__file__).resolve().parents[2] / "contracts" / "report.schema.json"
SQL = "select report_no || E'\\t' || 'report_json' || E'\\t' || report_json::text from public.reports " \
      "union all select report_no || E'\\t' || 'llm_draft_json' || E'\\t' || llm_draft_json::text from public.reports order by 1"


def main() -> int:
    cmd = shlex.split(os.environ.get("PSQL_CMD", "psql -At"))
    out = subprocess.run(cmd + ["-c", SQL], capture_output=True, text=True, check=True).stdout
    validator = Draft202012Validator(json.loads(SCHEMA.read_text()), format_checker=FormatChecker())
    n = bad = 0
    for line in filter(None, out.splitlines()):
        no, col, raw = line.split("\t", 2)
        n += 1
        for err in validator.iter_errors(json.loads(raw)):
            bad += 1
            print(f"FAIL {no} {col}: {'/'.join(map(str, err.path))}: {err.message}")
    if n == 0:
        print("FAIL: no reports found")
        return 1
    print(f"validated {n} report documents, {bad} errors")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
