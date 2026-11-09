"""``argus-cli bridge run``: the cloud bridge for the teammates' hat code (see argus/bridge/__init__.py)."""

from __future__ import annotations

import logging
import signal
import threading
from pathlib import Path
from typing import Annotated

import typer

from argus.config import ConfigError, load_config

bridge_app = typer.Typer(add_completion=False, no_args_is_help=True,
                         help="Cloud bridge: teammates' hat.main -> our queue and uploader")


@bridge_app.command("run")
def run(
    config: Annotated[Path | None, typer.Option("--config", "-c", help="config.yaml (default: device/config.yaml)")] = None,
    launch: Annotated[bool | None, typer.Option("--launch/--no-launch",
                                                help="start `python -m hat.main` as a child (default: bridge.launch)")] = None,
    tof_log: Annotated[Path | None, typer.Option("--tof-log", help="with --no-launch: file their stdout is piped to")] = None,
    from_start: Annotated[bool, typer.Option("--from-start", help="first run: read events.jsonl from the start, not the end")] = False,
    uploader: Annotated[bool, typer.Option("--uploader/--no-uploader", help="run the normal uploader thread")] = True,
) -> None:
    """Run until SIGTERM / Ctrl-C. Needs a paired device (`argus-cli pair`) to upload; queues locally otherwise."""
    from argus.bridge.service import Bridge

    logging.getLogger("argus.bridge").setLevel(logging.INFO)
    try:
        cfg = load_config(config)
    except ConfigError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    bridge = Bridge(cfg, launch=launch, tof_log=tof_log, from_start=from_start)
    stop_upload = None
    if uploader:
        from argus.uploader import start_uploader

        stop_upload = start_uploader(config)  # the same thread `argus-cli run` starts
    if not cfg.backend.base_url or not cfg.backend.device_token:
        typer.echo("backend not paired: events are queued locally (run `argus-cli pair` to upload)")
    stop = threading.Event()
    prev = [signal.signal(s, lambda *_: stop.set()) for s in (signal.SIGTERM, signal.SIGINT)]
    typer.echo(f"argus bridge running: events={bridge.events_path} launch={bridge.do_launch}; Ctrl-C to stop")
    try:
        bridge.run(stop)
    finally:
        for s, h in zip((signal.SIGTERM, signal.SIGINT), prev, strict=True):
            signal.signal(s, h)
        if stop_upload:
            stop_upload()
