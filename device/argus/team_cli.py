"""``argus-cli team check|init``: plug a teammate's script in without hardware (spec §5.5).

``check`` feeds pasted real output through the same mapping the hat uses and prints each parsed sample in
contract units, or the exact error. ``init`` writes a starter ``mapping.yaml`` into the module's team folder.
"""

from __future__ import annotations

import importlib
import statistics
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from argus.config import ConfigError, load_config
from argus.contracts import MODULE_NAMES, EnvSample, ImuSample, RangeSample, TofSample

team_app = typer.Typer(no_args_is_help=True, add_completion=False,
                       help="Plug in a teammate's hardware script: check its output, write a starter mapping")

MODULE_ARG = Annotated[str, typer.Argument(help=f"one of {', '.join(MODULE_NAMES)}")]


def _fail(msg: str) -> typer.Exit:
    typer.secho(msg, fg=typer.colors.RED, err=True)
    return typer.Exit(1)


def _team_dir(module: str) -> Path:
    if module not in MODULE_NAMES:
        raise _fail(f"unknown module {module!r}; choose from {', '.join(MODULE_NAMES)}")
    return Path(importlib.import_module(f"hardware.team.{module}").__file__).parent  # type: ignore[arg-type]


def describe(s: Any) -> str:
    """One sample in contract units, for humans."""
    if isinstance(s, ImuSample):
        return (f"imu    accel g=({s.ax:+.3f}, {s.ay:+.3f}, {s.az:+.3f})  "
                f"gyro deg/s=({s.gx:+.1f}, {s.gy:+.1f}, {s.gz:+.1f})")
    if isinstance(s, RangeSample):
        return f"range  {s.sensor_id}  " + ("no echo" if s.range_mm is None else f"{s.range_mm:.1f} mm")
    if isinstance(s, TofSample):
        return f"hat_on {s.status}  " + ("" if s.range_mm is None else f"{s.range_mm:.1f} mm")
    if isinstance(s, EnvSample):
        def f(v):
            return None if v is None else round(v, 2)

        return f"env    co2={f(s.co2_ppm)} ppm  temp={f(s.temp_c)} C  rh={f(s.rh_pct)} %"
    return repr(s)


@team_app.command("check")
def check(
    module: MODULE_ARG,
    sample: Annotated[Path | None, typer.Option("--sample", "-s", help="text file with 10-20 lines of real output "
                                                                      "(default: read stdin)")] = None,
    mapping: Annotated[Path | None, typer.Option("--mapping", "-m", help="mapping file (default: the module's "
                                                                        "mapping.yaml)")] = None,
    config: Annotated[Path | None, typer.Option("--config", "-c", help="config whose modules.<m>.options "
                                                                      "are merged over the mapping")] = None,
) -> None:
    """Run pasted output through the mapping and print each reading in contract units (no hardware)."""
    from hardware.team._mapping import Mapper, MappingError, load_mapping, unknown_keys

    adir = _team_dir(module)
    if module not in ("imu", "range", "hat_on", "env"):
        raise _fail(f"{module} has no output to check; see hardware/team/{module}/README.md")
    try:
        options = dict(load_config(config).modules[module].options)
    except ConfigError as e:
        raise _fail(str(e)) from None
    if mapping:
        options["mapping_file"] = str(mapping)
    try:
        m = load_mapping(module, adir, options)
        mapper = Mapper(module, m)
    except MappingError as e:
        raise _fail(f"mapping problem: {e}") from None
    for w in unknown_keys(module, m):
        typer.secho(f"warning: {w}", fg=typer.colors.YELLOW)
    src = m.get("command") or m.get("python")
    typer.echo(f"{module}: format={mapper.format}, source={src if src else '(none set yet: module falls back)'}")
    if sample is None and sys.stdin.isatty():
        raise _fail("give --sample file.txt, or pipe the output in:  cat sample.txt | argus-cli team check " + module)
    try:
        text = sample.read_text() if sample else sys.stdin.read()
    except OSError as e:
        raise _fail(f"cannot read {sample}: {e}") from None

    parsed: list[Any] = []
    errors = lines = 0
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        lines += 1
        try:
            got = mapper.parse(line, 0)
        except Exception as e:  # noqa: BLE001
            errors += 1
            typer.secho(f"line {n}: ERROR {e}", fg=typer.colors.RED)
            continue
        if not got:
            typer.echo(f"line {n}: skipped (matches `ignore`)")
        for s in got:
            parsed.append(s)
            typer.echo(f"line {n}: {describe(s)}")
    typer.echo(f"\n{len(parsed)} readings from {lines} lines, {errors} errors")
    if module == "imu" and parsed:
        mag = statistics.median((s.ax**2 + s.ay**2 + s.az**2) ** 0.5 for s in parsed)
        if not 0.8 <= mag <= 1.2:
            typer.secho(f"warning: median |accel| is {mag:.2f} g. A still hat reads about 1 g. If it was still, "
                        "check accel_unit (m/s2? raw counts: lsb:16384?).", fg=typer.colors.YELLOW)
    if errors or not parsed:
        raise typer.Exit(1)


@team_app.command("init")
def init(
    module: MODULE_ARG,
    fmt: Annotated[str, typer.Option("--format", "-f", help="json or text (what their script prints)")] = "json",
    script: Annotated[str, typer.Option(help="file name of their script in vendor/, without .py")] = "their_script",
    force: Annotated[bool, typer.Option("--force", help="overwrite an existing mapping.yaml")] = False,
) -> None:
    """Write a starter mapping.yaml into hardware/team/<module>/ for you to fill in."""
    from hardware.team._starters import starter

    if fmt not in ("json", "text"):
        raise _fail("--format must be json or text")
    path = _team_dir(module) / "mapping.yaml"
    if path.exists() and not force:
        raise _fail(f"{path} already exists (use --force to overwrite)")
    path.write_text(starter(module, fmt, script))
    typer.echo(f"wrote {path}\nnext: put your script in {path.parent / 'vendor'}, edit the mapping, then\n"
               f"  argus-cli team check {module} --sample sample.txt")
