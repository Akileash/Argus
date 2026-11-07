import json
import pathlib

CONTRACTS = pathlib.Path(__file__).resolve().parents[3] / "contracts"


def test_contracts_load():
    for name in ("event", "sensor_trace", "report"):
        json.loads((CONTRACTS / f"{name}.schema.json").read_text())
