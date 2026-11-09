"""``argus-cli``: run the stack, hot-swap modules, bench-test hardware, drive mocks (spec §5.4-§5.8).

Commands: ``run``, ``reload``, ``test``, ``scenario``, ``pair``, ``upload``, plus the tuning set
(``record``, ``sweep``, ``eval``) and ``trigger``, all defined in this module.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import signal
import threading
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
from argus.supervisor import (
    DEFAULT_CONFIG,
    EXCLUSIVE_MODULES,
    STREAM_FOR,
    ModuleLoadError,
    Supervisor,
    load_impl,
    shadow,
)

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


from argus.bridge.cli import bridge_app

app.add_typer(bridge_app, name="bridge")


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

    sup = Supervisor(cfg, bus, MonotonicClock(), on_event=on_event, scenario=scenario,
                     config_path=config or DEFAULT_CONFIG)
    stops = _wire_pipeline(sup, bus, cfg)  # <- HOOK (see above)
    from argus.uploader import start_uploader  # L4: idles quietly while unpaired, never exits

    stops.append(start_uploader(config))
    sup.start()
    srv = None
    if not cfg.backend.base_url or not cfg.backend.device_token:
        typer.echo("backend not paired: detecting and queueing events locally (run `argus-cli pair` to upload)")
    typer.echo(f"argus running (scenario={scenario}); Ctrl-C to stop")
    t_end = time.monotonic() + duration if duration else None
    # `systemctl stop/restart` sends SIGTERM: treat it like Ctrl-C so open events get flushed (clip + trace
    # written, queue committed) instead of being lost mid post-roll.
    stopping = threading.Event()
    prev_term = signal.signal(signal.SIGTERM, lambda signum, frame: stopping.set())
    try:
        if control_socket:
            srv = control.ControlServer(sup)
            srv.start()
        while (t_end is None or time.monotonic() < t_end) and not stopping.is_set():
            if stopping.wait(1.0):
                break
            counts = {s: len(bus.window(s, 0, 1 << 62)) for s in ("imu", "range", "tof", "env")}
            impls = {n: v["impl"] for n, v in sup.status().items()}
            typer.echo(f"buffered {counts} impls {impls}")
    except KeyboardInterrupt:
        pass
    except control.ControlSocketBusy as e:
        raise _fail(str(e)) from None  # the finally block below still flushes events and stops everything
    finally:
        signal.signal(signal.SIGTERM, prev_term)
        if stopping.is_set():
            typer.echo("SIGTERM received: flushing open events, then exiting")
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
    if module in EXCLUSIVE_MODULES:
        typer.echo(f"note: {module} owns exclusive hardware, so the old impl was stopped before the new one "
                   "started and the new one passed its health check; there was a short gap in its data.")


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
    stops = _wire_pipeline(sup, bus, cfg)  # <- HOOK
    sup.start()
    try:
        time.sleep(span / speed + 0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for stop in stops:  # flush open events (clips/traces) before the modules go away
            stop()
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
    try:
        order = list(load_config(None).modules["range"].options.get("order") or ["R2", "R3"])
    except ConfigError:
        order = ["R2", "R3"]
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
    typer.echo("   t[s]   |a|[g]   gyro[dps]   ToF[mm]   " + "  ".join(f"{s:>6}" for s in order))
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
                              for k in order)
            typer.echo(f"{t:7.2f}  {imu_row[0]:6.2f}  {imu_row[1]:9.1f}  {tof_row!s:>8}   {cells}")
    if record:
        write_recording(record, all_samples, {"source": f"mock:{name}"})
        typer.echo(f"recording written: {record} ({len(all_samples)} samples)")


@app.command()
def pair(
    url: Annotated[str | None, typer.Option("--url", help="backend base URL, e.g. https://argus.example.com")] = None,
    token: Annotated[str | None, typer.Option("--token", help="device token shown once in the dashboard")] = None,
    config: ConfigOpt = None,
    check: Annotated[bool, typer.Option("--check", help="only verify the configured credentials")] = False,
    verify: Annotated[bool, typer.Option("--verify/--no-verify", help="call GET /api/devices/me before saving")] = True,
) -> None:
    """Store backend URL + device token in config.yaml and verify them (spec §5.8 device auth)."""
    from argus import pairing

    path = pairing.resolve_config_path(config)
    if check:
        cfg = _config(config)
        if not (cfg.backend.base_url and cfg.backend.device_token):
            raise _fail("not paired: backend.base_url / backend.device_token are blank (run `argus-cli pair`)")
        try:
            me = pairing.fetch_me(cfg.backend.base_url, cfg.backend.device_token, timeout_s=cfg.backend.timeout_s)
        except pairing.PairingError as e:
            raise _fail(str(e)) from None
        typer.echo(f"OK {pairing.describe(me)}")
        if warn := pairing.clock_warning(me):
            typer.secho(warn, fg=typer.colors.RED, err=True)
        return
    url = url or typer.prompt("Backend base URL")
    token = token or typer.prompt("Device token", hide_input=True)
    me = None
    if verify:
        try:
            me = pairing.fetch_me(url, token)
        except pairing.PairingError as e:
            raise _fail(f"{e}\nNothing was written. Use --no-verify to save anyway.") from None
    try:
        pairing.write_pairing(path, url, token, me["serial"] if me else None)
    except OSError as e:
        raise _fail(f"cannot write {path}: {e}") from None
    typer.echo(f"paired; wrote {path}")
    if me:
        typer.echo(pairing.describe(me))
        if warn := pairing.clock_warning(me):
            typer.secho(warn, fg=typer.colors.RED, err=True)
    typer.echo("a running `argus-cli run` notices the new token within ~10 s")


@app.command()
def upload(
    config: ConfigOpt = None,
    once: Annotated[bool, typer.Option("--once", help="drain the queue once and exit (ignores backoff timers)")] = False,
    status: Annotated[bool, typer.Option("--status", help="print queue depth, oldest age, last error")] = False,
    queue_dir: Annotated[Path | None, typer.Option("--queue-dir", help="override the queue directory")] = None,
) -> None:
    """Upload queued events to the backend (normally runs inside `argus-cli run`)."""
    from argus import uploader as up

    cfg = _config(config)
    u = up.Uploader(queue_dir or up.queue_dir_from_config(cfg), up.config_provider(config))
    if status:
        typer.echo(u.status_line())
        return
    if not once:
        typer.echo(f"uploader running on {u.queue_dir}; Ctrl-C to stop")
        try:
            u.run_forever()
        except KeyboardInterrupt:
            pass
        return
    if not u._settings().paired:
        raise _fail("not paired: run `argus-cli pair` first")
    deadline = time.time() + 120
    while True:
        counts = u.drain_once(ignore_backoff=True)
        typer.echo(json.dumps(counts))
        left = u.status()
        if left["pending"] == 0 or counts["skipped"] or time.time() > deadline:
            break
        time.sleep(2.0)
    typer.echo(u.status_line())
    raise typer.Exit(0 if left["pending"] == 0 and left["rejected"] == 0 else 1)


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


@app.command()
def record(
    label: Annotated[str, typer.Option(help="short name, used in the file name")],
    seconds: Annotated[float, typer.Option(help="stop after N seconds (0 = until Ctrl-C)")] = 0.0,
    notes: Annotated[str, typer.Option(help="free text for the labels sidecar")] = "",
    expect: Annotated[list[str] | None, typer.Option(help="expected event type[:t_s[:sensor]] (repeatable)")] = None,
    config: ConfigOpt = None,
    scenario: Annotated[str, typer.Option(help="scenario for mock impls")] = "idle",
    out_dir: Annotated[Path | None, typer.Option(help="default: device/recordings")] = None,
) -> None:
    """Record raw sensor streams to recordings/<timestamp>_<label>.jsonl + .labels.json (for eval/sweep)."""
    from argus.recorder import Recorder, parse_expect, write_labels

    try:
        expected = [parse_expect(e) for e in expect or []]
    except ValueError as e:
        raise _fail(str(e)) from None
    cfg = _config(config)
    bus = SampleBus(cfg.bus.history_s)
    sup = Supervisor(cfg, bus, MonotonicClock(), on_event=lambda r: typer.echo(f"EVENT {r}"), scenario=scenario,
                     config_path=config)
    rec = Recorder(bus, out_dir, label, meta={"modules": {n: c.impl for n, c in cfg.modules.__dict__.items()
                                                          if hasattr(c, "impl")}})
    sup.start()
    typer.echo(f"recording -> {rec.path} ({'until Ctrl-C' if not seconds else f'{seconds:g} s'})")
    t_end = time.monotonic() + seconds if seconds else None
    try:
        while t_end is None or time.monotonic() < t_end:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        sup.stop()
        path = rec.close()
    write_labels(rec.labels_path, label, expected, notes)
    typer.echo(f"saved {rec.count} samples to {path}\nlabels: {rec.labels_path}")


@app.command()
def trigger(
    event_type: Annotated[str, typer.Argument(metavar="TYPE", help="struck_by_near_miss | struck_by_contact | "
                                              "head_impact | fall | hat_knocked_off | hat_removed")],
    config: ConfigOpt = None,
    scenario: Annotated[str, typer.Option(help="standalone mode: scenario for mock impls")] = "idle",
    warmup: Annotated[float, typer.Option(help="standalone mode: seconds to buffer before triggering")] = 3.0,
) -> None:
    """Synthesize an event now (real clip from the camera ring) for stage backup (spec §7).

    Uses the running `argus-cli run` if there is one; otherwise starts the stack, triggers, waits for the
    post-roll, queues the event and exits."""
    from argus.events.synthetic import TRIGGER_TYPES, synthesize

    if event_type not in TRIGGER_TYPES:
        raise _fail(f"unknown event type {event_type!r}; choose from {', '.join(TRIGGER_TYPES)}")
    try:
        resp = control.send({"cmd": "trigger", "type": event_type})
    except ConnectionError:
        resp = None
    if resp is not None:
        if not resp["ok"]:
            raise _fail(f"trigger failed: {resp['error']}")
        typer.echo(f"triggered {event_type} in the running hat: event {resp['event_id']} queues after the "
                   f"{resp['post_roll_s']:g} s post-roll")
        return
    from argus.pipeline import Pipeline

    cfg = _config(config)
    bus = SampleBus(cfg.bus.history_s)
    sup = Supervisor(cfg, bus, MonotonicClock(), scenario=scenario, config_path=config)
    pipe = Pipeline(sup, bus, cfg)
    sup.start()
    pipe.start()
    try:
        typer.echo(f"no running hat found; standalone: buffering {warmup:g} s ...")
        time.sleep(warmup)
        eid = pipe.inject(synthesize(event_type, sup.clock.now_ns()))
        if eid is None:
            raise _fail("trigger ignored (cooldown)")
        if event_type != "hat_removed":
            typer.echo(f"event {eid}: recording {cfg.events.post_roll_s:g} s post-roll ...")
            time.sleep(cfg.events.post_roll_s + 0.5)
    finally:
        pipe.stop()
        sup.stop()
    typer.echo(f"queued in {pipe.queue.root}: {', '.join(p.name for p in pipe.queue.ready())}")


from argus.team_cli import team_app

app.add_typer(team_app, name="team")


if __name__ == "__main__":  # pragma: no cover
    app()
