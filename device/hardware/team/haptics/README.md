# team/haptics: plug in your script

Status: **not configured yet.** Until `mapping.yaml` has a `python`, `command` or `port`, `create()` raises
`NotImplementedError("team impl for haptics not pasted yet")` and the supervisor falls back (spec section 5.4).

## Steps (spec sections 2.1, 2.4, 5.5)

1. Copy your script into `vendor/`. Do not change it.
2. `cd device && uv run argus-cli team init haptics` writes a starter `mapping.yaml` (everything commented out).
3. Uncomment ONE way of driving the motors (below).
4. On the Pi: `uv run pytest tests/contract --module haptics --impl team`, then `uv run argus-cli buzz both`.
5. Turn it on in `device/config.local.yaml` (gitignored): `modules: {haptics: {impl: team, fallback: mock}}`,
   then `uv run argus-cli reload haptics`.
6. Send the integrator the hand-off template below, then open a pull request that touches only
   `device/hardware/team/haptics/`.

```
Module: haptics
Wiring: pins / I2C address / power used
Libraries + versions: (pip freeze excerpt)
What it outputs: 10-20 lines of real output, pasted as is
Units + rate: e.g. "distance in cm, ~10 readings/s"
Known quirks: e.g. "sensor 3 reads 0 sometimes", "needs 2s warmup"
```

## What the module must deliver

`set(motor, intensity)` with motor `top` or `back` and intensity 0.0 to 1.0, and `off_all()`. Patterns (pulse
rates, the 1.5 s impact buzz) live in the event code, NOT here: the driver only sets a strength. It never
raises. The supervisor checks it once a second with a zero-intensity self-test and falls back if that fails.

## Way 1: call your Python function (a subprocess is not needed)

```yaml
python: vendor.motors            # a module in vendor/
call: set_duty                   # called as set_duty(first_arg, value)
motors: { top: 18, back: 19 }    # contract name -> what your function expects as the first argument
scale: 100                       # intensity 0..1 is multiplied by this (0..100 duty)
setup: init_pins                 # optional, called once; teardown: cleanup_pins also works
```

## Way 2: send a line to your script's stdin (subprocess)

```yaml
command: [python, vendor/motor_daemon.py]
line: "H,{motor},{pwm}\n"       # placeholders: {motor} {level} (0..1) {pwm} (0..255) {pct} (0..100)
```

## Way 3: the Arduino over USB serial (spec section 2.4, fallback A)

```yaml
port: /dev/ttyACM0
baud: 115200
line: "H,{motor},{pwm}\n"
```

Needs `pyserial` (`uv pip install pyserial` on the Pi). `motors: { top: top, back: back }` renames the motors
if the sketch expects other names, for example `both`.

If a call raises or the script dies, `health()` turns not-ok with the reason and the supervisor falls back.
