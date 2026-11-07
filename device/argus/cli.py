"""``argus-cli``: run the stack, hot-swap modules, bench-test hardware, drive mocks (spec §5.4-§5.8).

Planned commands owned by other tasks (do NOT implement here): ``record``, ``sweep``, ``eval``
(threshold tuning), ``trigger`` (synthetic event with a real clip), ``pair`` (backend pairing).
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer

from argus import control
from argus.bench import Bench
from argus.bus import SampleBus
from argus.config import ArgusConfig, ConfigError, ModuleCfg, load_config
from argus.contracts import MODULE_NAMES, ModuleContext, MonotonicClock
from argus.supervisor import STREAM_FOR, ModuleLoadError, Supervisor, load_impl, shadow

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Argus smart hard hat: device CLI")

ConfigOpt = Annotated[Path | None, typer.Option("--config", "-c", help="config.yaml (default: device/config.yaml)")]

# ============================================================================================
# HOOK: L2 detectors + L3 event manager + L4 uploader wiring. Other tasks fill this in, either by
# appending a function to PIPELINE_HOOKS or by providing ``argus/pipeline.py`` with
# ``wire(sup, bus, cfg) -> Callable[[], None] | None`` (optional stop function). ``run`` calls
# every hook after the supervisor is built and before it starts.
# ============================================================================================
PipelineHook = Callable[[Supervisor, SampleBus, ArgusConfig], "Callable[[], None] | None"]
PIPELINE_HOOKS: list[PipelineHook] = []


def _wire_pipeline(sup: Supervisor, bus: SampleBus, cfg: ArgusConfig) -> list[Callable[[], None]]:
    hooks = list(PIPELINE_HOOKS)
    try:
        hooks.append(importlib.import_module("argus.pipeline").wire)
    except ModuleNotFoundError:
        pass
    return [stop for h in hooks if (stop := h(sup, bus, cfg)) is not None]


@app.callback()
def _main(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")


# ---------------------------------------------------------------- helpers
def _fail(msg: str) -> typer.Exit:
    typer.secho(msg, fg=typer.colors.RED, err=True)
    return typer.Exit(1)


def _module(name: str) -> str:
    if name not in MODULE_NAMES:
        raise _fail(f"unknown module {name!r}; choose from {', '.join(MODULE_NAMES)}")
    return name


def _config(path: Path | None) -> ArgusConfig:
    try:
        return load_config(path)
    except ConfigError as e:
        raise _fail(str(e)) from None


def _impl_for(cfg: ArgusConfig, module: str, impl: str | None, scenario: str | None, clock: Any) -> Any:
    mc = cfg.modules[module]
    try:
        return load_impl(module, impl or mc.impl, ModuleContext(clock, dict(mc.options), scenario))
    except ModuleLoadError as e:
        raise _fail(str(e)) from None


# ---------------------------------------------------------------- commands
@app.command()
def run(
    config: ConfigOpt = None,
    scenario: Annotated[str, typer.Option(help="scenario for mock impls")] = "idle",
    duration: Annotated[float, typer.Option(help="stop after N seconds (0 = until Ctrl-C)")] = 0.0,
    control_socket: Annotated[bool, typer.Option("--control/--no-control", help="enable `reload` channel")] = True,
) -> None:
    """Start the supervisor and sample bus (plus detectors/events once wired)."""
    cfg = _config(config)
    bus = SampleBus(cfg.bus.history_s)
    events: list[dict[str, Any]] = []

    def on_event(rec: dict[str, Any]) -> None:
        events.append(rec)
        typer.secho(f"EVENT {json.dumps(rec)}", fg=typer.colors.YELLOW)

    sup = Supervisor(cfg, bus, MonotonicClock(), on_event=on_event, scenario=scenario, config_path=config)
    stops = _wire_pipeline(sup, bus, cfg)  # <- HOOK (see above)
    sup.start()
    srv = control.ControlServer(sup) if control_socket else None
    if srv:
        srv.start()
    typer.echo(f"argus running (scenario={scenario}); Ctrl-C to stop")
    t_end = time.monotonic() + duration if duration else None
    try:
        while t_end is None or time.monotonic() < t_end:
            time.sleep(1.0)
            counts = {s: len(bus.window(s, 0, 1 << 62)) for s in ("imu", "range", "tof", "env")}
            impls = {n: v["impl"] for n, v in sup.status().items()}
            typer.echo(f"buffered {counts} impls {impls}")
    except KeyboardInterrupt:
        pass
    finally:
        for stop in stops:
            stop()
        if srv:
            srv.stop()
        sup.stop()


@app.command()
def reload(
    module: str,
    impl: Annotated[str | None, typer.Option(help="override impl instead of re-reading config.yaml")] = None,
) -> None:
    """Hot-swap one module in a running `argus-cli run` (re-reads config.yaml)."""
    _module(module)
    try:
        resp = control.send({"cmd": "reload", "module": module, "impl": impl})
    except ConnectionError as e:
        raise _fail(str(e)) from None
    if not resp["ok"]:
        raise _fail(f"reload failed: {resp['error']}")
    typer.echo(f"{module} now on {resp['impl']} (swapped in {resp['seconds'] * 1000:.0f} ms)")


@app.command()
def test(
    module: str,
    config: ConfigOpt = None,
    impl: Annotated[str | None, typer.Option(help="impl to test (default: from config)")] = None,
    scenario: str = "idle",
    interactive: Annotated[bool, typer.Option(help="run the steps that need a human")] = False,
) -> None:
    """Bench self-test: live readings + pass/fail checklist (spec §5.6)."""
    _module(module)
    cfg = _config(config)
    clock = MonotonicClock()
    inst = _impl_for(cfg, module, impl, scenario, clock)
    checks = Bench(module, inst, clock, interactive, typer.echo).run()
    raise typer.Exit(1 if any(c.status == "FAIL" for c in checks) else 0)


@app.command()
def buzz(
    where: Annotated[str, typer.Argument(help="top | back | both")] = "both",
    config: ConfigOpt = None,
    impl: Annotated[str | None, typer.Option()] = None,
    seconds: float = 0.6,
    level: float = 1.0,
) -> None:
    """Buzz the haptic motors (bench check)."""
    if where not in ("top", "back", "both"):
        raise _fail("where must be top, back or both")
    cfg = _config(config)
    drv = _impl_for(cfg, "haptics", impl, None, MonotonicClock())
    drv.start()
    try:
        for m in ("top", "back") if where == "both" else (where,):
            drv.set(m, level)
        time.sleep(seconds)
    finally:
        drv.off_all()
        drv.stop()
    typer.echo(f"buzzed {where} at {level:.0%} for {seconds}s")


@app.command("shadow")
def shadow_cmd(
    module: str,
    config: ConfigOpt = None,
    a: Annotated[str | None, typer.Option(help="first impl (default: configured impl)")] = None,
    b: Annotated[str | None, typer.Option(help="second impl (default: configured fallback)")] = None,
    seconds: float = 10.0,
    alternate: Annotated[bool, typer.Option(help="alternate 1 s windows if the bus can't be shared")] = False,
    scenario: str = "idle",
) -> None:
    """Run two impls of a streaming module and write a diff log (spec §5.4 step 3)."""
    _module(module)
    if module not in STREAM_FOR:
        raise _fail(f"{module} is exclusive-access: shadow only works for {list(STREAM_FOR)}; use A/B bench runs")
    cfg = _config(config)
    mc = cfg.modules[module]
    impl_a, impl_b = a or mc.impl, b or mc.fallback
    if impl_b is None:
        raise _fail(f"modules.{module}.fallback is not set; pass --b <impl>")
    try:
        path = shadow(module, impl_a, impl_b, seconds, options=dict(mc.options), scenario=scenario,
                      alternate=alternate)
    except ModuleLoadError as e:
        raise _fail(str(e)) from None
    typer.echo(f"diff log: {path}")
    typer.echo(json.dumps(json.loads(path.read_text().splitlines()[-1])["summary"], indent=2))


@app.command()
def replay(
    file: Path,
    speed: Annotated[float, typer.Option(help="1 = real time, 10 = ten times faster")] = 1.0,
    config: ConfigOpt = None,
) -> None:
    """Replay a .jsonl recording through the real bus (imu/range/hat_on/env use the replay impls)."""
    if not file.exists():
        raise _fail(f"recording not found: {file}")
    from hardware.replay.recording import read_recording

    samples = list(read_recording(file))
    span = (max(s.t_ns for s in samples) - min(s.t_ns for s in samples)) / 1e9
    cfg = _config(config)
    for m in ("imu", "range", "hat_on", "env"):
        setattr(cfg.modules, m, ModuleCfg(impl="replay", options={"file": str(file), "speed": speed}))
    bus = SampleBus(max(cfg.bus.history_s, span + 1))
    sup = Supervisor(cfg, bus, MonotonicClock(), on_event=lambda r: typer.echo(f"EVENT {r}"), config_path=None)
    _wire_pipeline(sup, bus, cfg)  # <- HOOK
    sup.start()
    try:
        time.sleep(span / speed + 0.5)
    except KeyboardInterrupt:
        pass
    finally:
        sup.stop()
    typer.echo(f"replayed {file.name}: " + ", ".join(
        f"{s}={len(bus.window(s, 0, 1 << 62))}" for s in ("imu", "range", "tof", "env")))


@app.command()
def scenario(
    name: Annotated[str | None, typer.Argument(help="scenario name (omit with --list)")] = None,
    list_: Annotated[bool, typer.Option("--list", help="list available scenarios")] = False,
    seconds: Annotated[float | None, typer.Option(help="default: the scenario duration")] = None,
    record: Annotated[Path | None, typer.Option(help="also write a .jsonl recording")] = None,
    every: Annotated[float, typer.Option(help="print a summary row every N s")] = 0.5,
) -> None:
    """Run all mock sensors through a scenario (virtual time) and print what the hat would sense."""
    from hardware.mock.clock import VirtualClock
    from hardware.mock.scenario import list_scenarios, load_scenario
    from hardware.replay.recording import write_recording

    if list_ or name is None:
        typer.echo("\n".join(list_scenarios()))
        return
    try:
        sc = load_scenario(name)
    except FileNotFoundError as e:
        raise _fail(str(e)) from None
    clock = VirtualClock()
    ctx = ModuleContext(clock, {}, name)
    mods = {m: load_impl(m, "mock", ctx) for m in ("imu", "range", "hat_on", "env")}
    for m in mods.values():
        m.start()
    all_samples: list[Any] = []
    last_range: dict[str, float | None] = {}
    steps = int((seconds or sc.duration_s) / 0.01)
    typer.echo(f"scenario {name}: {sc.spec.description.strip()}")
    typer.echo(f"expected: {[(e.type, e.sensors, e.t_window_s) for e in sc.expected] or 'no events'}")
    typer.echo("   t[s]   |a|[g]   gyro[dps]   ToF[mm]   " + "  ".join(f"{s:>6}" for s in ("R1", "R2", "R3", "R4", "TOP")))
    next_print = 0.0
    imu_row = (0.0, 0.0)
    tof_row: Any = None
    for i in range(steps):
        clock.advance(0.01)
        for m, inst in mods.items():
            got = inst.read()
            all_samples.extend(got)
            for s in got:
                if m == "imu":
                    imu_row = ((s.ax**2 + s.ay**2 + s.az**2) ** 0.5, (s.gx**2 + s.gy**2 + s.gz**2) ** 0.5)
                elif m == "range":
                    last_range[s.sensor_id] = s.range_mm
                elif m == "hat_on":
                    tof_row = s.range_mm if s.status == "ok" else "off"
        t = (i + 1) * 0.01
        if t >= next_print:
            next_print += every
            cells = "  ".join(f"{'--' if last_range.get(k) is None else f'{last_range[k]:.0f}':>6}"
                              for k in ("R1", "R2", "R3", "R4", "TOP"))
            typer.echo(f"{t:7.2f}  {imu_row[0]:6.2f}  {imu_row[1]:9.1f}  {tof_row!s:>8}   {cells}")
    if record:
        write_recording(record, all_samples, {"source": f"mock:{name}"})
        typer.echo(f"recording written: {record} ({len(all_samples)} samples)")


# ---------------------------------------------------------------- tuning (docs/TUNING.md)
RecordingsOpt = Annotated[
    Path | None, typer.Option("--recordings", "-r", help="dir of .jsonl + .labels.json (default: device/recordings)")
]


def _tuning_dataset(recordings: Path | None) -> list[Path]:
    from argus.tuning.evaluate import DEFAULT_RECORDINGS_DIR, discover

    d = recordings or DEFAULT_RECORDINGS_DIR
    if not d.is_dir():
        raise _fail(f"recordings directory not found: {d}")
    labelled, unlabelled = discover(d)
    for p in unlabelled:
        typer.secho(f"skipping {p.name}: no .labels.json sidecar", fg=typer.colors.YELLOW, err=True)
    if not labelled:
        raise _fail(f"no labelled recordings in {d} (try `argus-cli export-scenarios --out {d}`)")
    return labelled


@app.command("eval")
def eval_cmd(
    recordings: RecordingsOpt = None,
    config: ConfigOpt = None,
    tolerance: Annotated[float, typer.Option(help="match window around a labelled time, seconds")] = 0.75,
    json_out: Annotated[bool, typer.Option("--json", help="print the scorecard as JSON")] = False,
) -> None:
    """Score the detectors (thresholds from config.yaml) against labelled recordings."""
    from argus.tuning.evaluate import evaluate, format_scorecard, load_dataset
    from argus.tuning.runner import load_thresholds

    paths = _tuning_dataset(recordings)
    try:
        th = load_thresholds(config)
    except ConfigError as e:
        raise _fail(str(e)) from None
    sc = evaluate(load_dataset(paths), th, tolerance)
    typer.echo(json.dumps(sc.to_dict(), indent=2) if json_out else format_scorecard(sc))


@app.command()
def sweep(
    param: Annotated[list[str], typer.Option("--param", "-p", help="section.key=v1,v2,... (repeatable)")],
    recordings: RecordingsOpt = None,
    config: ConfigOpt = None,
    max_fa_per_hour: Annotated[float, typer.Option(help="false-alarm budget for the objective")] = 1.0,
    top: Annotated[int, typer.Option(help="rows to print")] = 10,
    tolerance: float = 0.75,
    workers: Annotated[int | None, typer.Option(help="processes (default: CPU count for big grids)")] = None,
    apply: Annotated[bool, typer.Option("--apply", help="write the best values into config.yaml")] = False,
) -> None:
    """Grid-search threshold values on labelled recordings; print the best and the YAML to paste."""
    from argus.config import DEFAULT_CONFIG_PATH
    from argus.tuning.runner import load_thresholds
    from argus.tuning.sweep import apply_to_config, format_results, parse_param, yaml_snippet
    from argus.tuning.sweep import sweep as run_sweep

    paths = _tuning_dataset(recordings)
    try:
        params = dict(parse_param(p) for p in param)
        base = load_thresholds(config)
        results = run_sweep(paths, base, params, max_fa_per_hour, tolerance, workers)
    except (ValueError, KeyError, ConfigError) as e:
        raise _fail(str(e)) from None
    typer.echo(format_results(results, top, max_fa_per_hour))
    best = results[0]
    if not best.feasible:
        typer.secho(f"warning: no grid point meets FA/h <= {max_fa_per_hour:g}; showing the least bad.",
                    fg=typer.colors.YELLOW, err=True)
    typer.echo("\nbest values, paste into config.yaml:\n" + yaml_snippet(best.values))
    if apply:
        target = config or Path(os.environ.get("ARGUS_CONFIG") or DEFAULT_CONFIG_PATH)
        apply_to_config(target, best.values)
        typer.echo(f"\napplied to {target}")


@app.command("export-scenarios")
def export_scenarios(
    out: Annotated[Path, typer.Option(help="output directory")] = Path("recordings/scenarios"),
) -> None:
    """Write every mock scenario as a .jsonl recording + .labels.json (demo/test data for eval/sweep)."""
    from argus.tuning.export import export_all

    paths = export_all(out)
    typer.echo(f"wrote {len(paths)} recordings (+ labels) to {out}")


if __name__ == "__main__":  # pragma: no cover
    app()
