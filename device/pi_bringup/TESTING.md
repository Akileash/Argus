# Argus hard-hat IMU — recording checklist

**Legacy.** This checklist targets `imu_fall.py` (old fall/impact/hat_removed).  
Canonical runtime is `python -m hat.main` (wear-gated **fall-only**). See `HANDOFF.md`.

Use these after `calibrate-worn` only if you still need the legacy script:

```bash
cd ~/argus && source .venv/bin/activate
python imu_fall.py record --label LABEL --seconds N
# then (or instead) watch live:
python imu_fall.py monitor
```

Fill **Reported** from the `EVENT` JSON (`type`, `priority`, `hat_possibly_set_down`, `confidence`).

| # | Label (suggested) | Action | Seconds | Expected class | Reported | Notes |
|---|-------------------|--------|---------|----------------|----------|-------|
| 1 | `sit_still` | Hat on head, sit still | 20 | no event | | baseline worn noise |
| 2 | `stand_still` | Hat on head, stand still | 20 | no event | | |
| 3 | `walk_normal` | Walk at normal pace | 30 | no event | | periodic ~1–1.5 g ok |
| 4 | `walk_stairs` | Walk up/down stairs | 30 | no event / rare near_fall | | |
| 5 | `nod_yes` | Nod head vigorously | 15 | no event | | |
| 6 | `shake_head` | Shake head side to side | 15 | no event | | |
| 7 | `bend_over` | Bend to pick something up | 20 | no event | | gravity tilt without impact |
| 8 | `sit_hard` | Sit down hard onto a chair | 15 | no event | | ~2–2.5 g spike ok |
| 9 | `jump_land` | Small jump, land on feet | 15 | no event / near_fall | | should not be `fall` |
| 10 | `fall_mat` | Controlled fall onto a soft mat (hat on) | 20 | **fall** (high) | | best positive test |
| 11 | `stumble_recover` | Stumble but catch yourself | 15 | **near_fall** | | motion continues |
| 12 | `bump_beam` | Light bump of hat on a door frame | 15 | **near_fall** or low **impact** | | keep walking after |
| 13 | `toss_soft` | Toss hat onto a couch/cushion | 15 | **impact**, maybe `hat_possibly_set_down` | | |
| 14 | `set_down_gentle` | Gently place hat on a table | 20 | low **impact**, `hat_possibly_set_down: true` | | no free-fall |
| 15 | `set_down_hard` | Plunk hat onto table | 20 | low **impact**, `hat_possibly_set_down: true` | | |
| 16 | `knock_off` | Knock hat off head onto soft floor | 20 | **hat_removed** | | large tilt + still |
| 17 | `tap_sensor` | Finger tap on the IMU module | 10 | no event / near_fall | | sensor-only |
| 18 | `shake_sensor` | Hold board and shake hard | 10 | **near_fall** or **impact** | | sensor-only |
| 19 | `drop_30cm` | Drop from ~30 cm onto a cushion | 10 | **fall** or **impact** | | sensor-only |
| 20 | `freefall_catch` | Brief toss upward, catch soft | 10 | **near_fall** / **fall** if caught dead | | free-fall bonus path |

## Pass criteria (hackathon demo)

- Walking / nodding / sitting hard → **no `fall`**
- Mat fall → **`fall`**, priority high
- Set down on table → event logged with **`hat_possibly_set_down: true`**, priority low (never discarded)
- Knocked off → **`hat_removed`**
- Worn confidence may be wrong; it must **never** suppress an event

## After recording

Events append to `~/argus/events.jsonl`. CSVs land in `~/argus/recordings/`.
Re-tune named constants at the top of `imu_fall.py` if the **Reported** column disagrees with **Expected**.
