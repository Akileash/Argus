# Tuning the Argus detectors from real recordings

Plain-language guide for the team. No machine learning: you record labelled test runs on the real
hat, a script counts hits, misses and false alarms for a set of threshold values, and you keep the
values that score best. Everything lives in `device/config.yaml`.

All commands run from `device/` (`uv run argus-cli ...`).

## 1. Why we need this

- The mock sensors are physically consistent, but they cannot reproduce real sensor noise: ultrasonic
  echoes that jump, a hat that wobbles on the head, the IMU drifting while the wearer walks, cables
  tapping the shell. The default thresholds are good guesses; only real data tells us if they work.
- **The main risk is struck-by false alarms caused by the wearer's own motion.** When you walk
  backward toward a wall, the wall "approaches" the rear sensors at walking speed, just like a swinging
  board would. The detector subtracts the wearer's own speed (estimated from the IMU), and that estimate
  is imperfect. A hat that buzzes at the wearer for no reason gets taken off, so false alarms matter more
  than a slightly late warning.
- Every number we tune is a visible, explainable threshold. If a result looks wrong we can open the
  recording and see why.

## 2. What to record

Record **at least 20-30 labelled runs**, more is better. Suggested minimum:

| Run | Count | What to do | Label (`expected`) |
|---|---|---|---|
| Board swing from behind | 8 | Swing a board toward the back of the hat, inside the zone about 15° either side of straight behind, at different speeds. Stop it short of the hat. | `struck_by_near_miss` at the closest-approach time |
| Walk backward toward a wall | 5 | Walk backward at normal and brisk pace and stop at the wall. Do this with the hat on. | none (any event is a false alarm) |
| Drop the hat | 5 | Drop it from about 1 m onto a soft surface. | `fall` |
| Remove the hat | 3 | Take it off normally and put it on a table. | `hat_removed` |
| Knock the hat off | 3 | Hit the side so it comes off. | `head_impact`, `hat_knocked_off` |
| Normal work, no events | **5 min or more in total** | Walk, bend, turn, climb stairs, work at a bench, with the hat on. | none |

Why the long "normal work" part: false alarms are reported **per hour**. One false alarm in a 10 second
clip looks like 360 per hour. Five minutes of ordinary movement is the minimum that makes the false-alarm
number meaningful; 30 minutes is better. Record it in several different places and movement styles.

Tips:
- One event per recording, with 3-5 seconds of quiet at the start (the detectors calibrate on boot) and a
  few seconds at the end. Keep runs 15-40 s.
- Record the unglamorous runs too. A walking-backward run where nothing should happen is the most valuable
  recording for struck-by.
- Do not hand-pick: keep recordings where the detector fired when you did not expect it. Those are the
  point.

## 3. How to label

Each recording `name.jsonl` has a sidecar `name.labels.json` next to it:

```json
{
  "label": "board_swing_rear",
  "expected": [
    {"type": "struck_by_near_miss", "t_s": 4.1, "sensor": "R2"}
  ],
  "notes": "board from straight behind, about 2.5 m/s, stopped 20 cm short"
}
```

- Record with `argus-cli record --label board_swing_rear` and add the `expected` entries when asked
  (or edit the sidecar by hand). Format details: `device/hardware/replay/FORMAT.md`.
- `type` is one of `struck_by_near_miss`, `struck_by_contact`, `head_impact`, `fall`, `hat_knocked_off`,
  `hat_removed`.
- `t_s` is the time in seconds from the start of the recording (use `argus-cli replay` or read the
  timestamps). It is optional: without it the event may happen anywhere in the recording. For struck-by,
  use the moment of closest approach or contact; that is what makes the **warning lead time** meaningful.
- `sensor` is optional (`R2` or `R3` on the MVP hat); `sensors` takes a list. Use it only when you are sure.
- Also supported: `t_window_s: [lo, hi]` to accept anything inside a time window instead of `t_s +- 0.75 s`
  (used by the exported mock scenarios).
- A drop of the hat usually also produces a head impact on landing in the real world. If the scorecard
  flags it as a false alarm and you agree it really happened, label it. Label what physically happened.
- Recordings **without** a sidecar are skipped (with a warning). A sidecar with `"expected": []` means
  "nothing should fire here".

## 4. Run the scorecard

```
uv run argus-cli eval --recordings recordings/
```

No hat yet? Try the toolkit on the mock scenarios:

```
uv run argus-cli export-scenarios --out recordings/scenarios
uv run argus-cli eval --recordings recordings/scenarios
```

Example output (mock scenarios):

```
8 recordings, 1.3 min, tolerance +-0.75 s
type                 labelled  detected  TP  miss  FP  FA/h  rate  med err s  max err s  lead s
-------------------  --------  --------  --  ----  --  ----  ----  ---------  ---------  ------
struck_by_near_miss         1         1   1     0   0   0.0  100%      +0.00      +0.00    0.00
head_impact                 2         1   1     1   0   0.0   50%      -0.12      -0.12       -
fall                        1         1   1     0   0   0.0  100%      -0.54      -0.54       -
hat_knocked_off             1         1   1     0   0   0.0  100%      +1.30      +1.30       -
hat_removed                 1         1   1     0   0   0.0  100%      +1.40      +1.40       -
overall                     6         5   5     1   0   0.0   83%      +0.00      +1.40    0.00
```

How to read it:

| Column | Meaning |
|---|---|
| labelled | events you said should happen |
| detected | events the detectors reported (of the six event types) |
| TP | detected events that match a label (right type, right sensor if given, within the time tolerance) |
| miss | labelled events nothing matched. **A missed real hazard.** |
| FP | detections that match no label. **A false alarm.** |
| FA/h | false alarms per hour of recording. The number that decides whether workers will tolerate the hat |
| rate | TP / labelled |
| med / max err | detection time minus labelled time (negative = detected early). Large values mean timing drifted |
| lead s | struck-by only: seconds between the first warning (haptic buzz) and the labelled contact/closest approach. More is better |

Below the table, the `problems:` list names each recording with a miss or false alarm, so you can open it
and see why. `--json` prints the same data for scripts; `--tolerance 1.0` widens the matching window.

Only these event types are scored; the live `struck_by_warning` and `hat_on` detections are not events.

## 5. Sweep thresholds

A sweep tries every combination of the values you list and scores each on all recordings.

```
uv run argus-cli sweep \
  --param struck_by.ego_tau_s=1.0,1.5,2.0 \
  --param struck_by.min_track_samples=3,4
```

**The objective is explicit.** Among settings with at most `--max-fa-per-hour` false alarms per hour
(default 1.0), pick the highest detection rate. Ties go to the longer warning lead time, then fewer false
alarms, then the smallest change from the current config. Settings over the false-alarm budget always rank
below those inside it. If none fit, the tool warns you; do not apply such a result, record more data or
change a different parameter.

The tool prints the top rows (`--top N`) and the exact YAML to paste under `thresholds:` in `config.yaml`.
`--apply` writes the winning values into `config.yaml` itself, changing only those lines and keeping
comments and everything else. Review the change with `git diff`, then re-run `eval` to confirm.

Large grids use all CPU cores (`--workers N` to override). A 60 second recording takes a fraction of a
second per setting, so grids of hundreds of points are fine.

### Which parameters to sweep, and why

| Parameter (`struck_by.`) | In plain words | Try | Trade-off |
|---|---|---|---|
| `ego_tau_s` | How long the hat "remembers" its own walking speed before letting it fade. Longer memory follows brisk walking better. | 1.0, 1.5, 2.0 | Too short: walking backward fast looks like something approaching (false alarm). Too long: the speed estimate drifts. This is the first thing to try if walking-backward runs false-alarm. |
| `min_track_samples` | How many echoes in a row are needed before we trust a moving object. | 3, 4 | 3 is the MVP default and gives one more sweep (about 0.06 s) of warning than 4 at 2.5 m/s, where the first warning is otherwise very late (about 0.36 s before contact). 4 is steadier against echo glitches. |
| `v_obj_min_mps` | Minimum speed of the object itself, after subtracting the wearer's motion. | 1.0, 1.5, 2.0 | Lower catches slow swings but lets noise through. |
| `suppress_v_obj_min_mps` | Stricter speed needed when most of the apparent closing is the wearer's own movement. | 2.0, 2.5, 3.0 | Higher kills walking false alarms but can miss a board swung while you walk. |
| `suppress_ego_ratio` | How much of the closing speed must be explained by the wearer's motion before the stricter rule applies. | 0.4, 0.6, 0.8 | Lower is more cautious near walls. |
| `ttc_max_s` | Warn only when contact is predicted within this many seconds. | 1.0, 1.3, 1.6 | Higher warns earlier but fires more often. |
| `confirm_sweeps` | Consecutive confirmations required. | 2, 3 | 3 removes one-off glitches and costs one sweep of warning time. |
| `rotation_gate_dps` | Turning faster than this switches struck-by off (turning the head fools the sensors). | 45, 60, 90 | Lower is safer against false alarms but blind while turning. |

Other detectors (change one at a time): `fall_impact.impact_g` (head impact threshold; lower catches
softer knocks but also bumps the hat against a bench), `fall_impact.free_fall_min_ms` (how long the hat
must be weightless to count as a drop), `hat_state.off_delta_mm` and `hat_state.off_confirm_s` (how far
and how long the crown sensor must read before "hat removed").

Rule of thumb: sweep two parameters at a time. More combinations means more chance of picking a lucky
value that only fits your recordings.

## 6. When to stop tuning

Stop when all of these hold on recordings you did **not** use to pick the values:

- every labelled hazard is detected (struck-by 100% of board swings, falls, knocks);
- false alarms are 1 per hour or fewer over at least 30 minutes of ordinary work, and zero on the
  walking-backward runs;
- timing errors are within about 0.5 s and warning lead time is comfortably positive.

Practical guidance:
- Record a second, fresh batch to confirm. If the fresh batch scores much worse, the values were
  over-fitted; widen the grid steps or collect more varied data.
- If two settings score the same, keep the one closest to the current config (the tool does this).
- Do not chase the last miss by loosening several thresholds at once. Look at that recording instead.
- Once happy: `git diff device/config.yaml`, commit, copy the good recordings into
  `device/recordings/curated/` (the permanent regression set), and keep `uv run pytest -q` green
  (`tests/unit/test_config_keys.py` guards that every config key is actually read by a detector).
- Every future false alarm or miss in the field becomes a new labelled recording. The scorecard only gets
  more trustworthy over time.
