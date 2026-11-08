# Argus hat — on-device sensing

Raspberry Pi 4 hardhat stack:

- Dual rear **HC-SR04** (line-of-fire)
- **MPU-6050** IMU → wear-gated **fall-only** (no separate `impact` type)
- Dual **DRV2605L** ERM haptics
- **VL6180X** ToF wear (I2C3) — gates falls; dwell 5 s ON / 3 s OFF, near band 50–254 mm
- Pi Camera clips on fall
- **SCD41** air lives in sibling package `~/argus/pi_sensors` (I2C1)

```bash
cd ~/argus
source .venv/bin/activate
```

## Wiring (current build)

| Part | Connection |
|------|------------|
| MPU-6050 (0x68) | **I2C3** SDA GPIO4 (7), SCL GPIO5 (29), VCC 3.3V, GND (with RIGHT DRV + ToF) |
| VL6180X (0x29) | **I2C3** with MPU / RIGHT DRV |
| LEFT DRV2605L (0x5A) | I2C1 SDA GPIO2 (3), SCL GPIO3 (5) |
| RIGHT DRV2605L (0x5A) | I2C3 with MPU |
| SCD41 (0x62) | **I2C1** (same bus as LEFT DRV) — run via `pi_sensors` |
| HC-SR04 LEFT | Trig GPIO23 (16), Echo GPIO24 (18) via 1k/2k divider |
| HC-SR04 RIGHT | Trig GPIO20 (38), Echo GPIO21 (40) via 1k/2k divider |

**Enable I2C bus 3** (once):

```bash
echo 'dtoverlay=i2c3,pins_4_5' | sudo tee -a /boot/firmware/config.txt
sudo reboot
# after reboot:
i2cdetect -y 1   # expect 5a (LEFT DRV), 62 (SCD41)
i2cdetect -y 3   # expect 5a (RIGHT DRV), 68 (MPU), 29 (ToF)
```

## Fall detection (current)

1. Freefall ≥ ~50 ms under `FREEFALL_G`, or hard hit ≥ `IMPACT_G` → candidate **fall**.
2. Soft landing after freefall still counts (no separate impact event).
3. **Wear gate**: look back 3 s of ToF; require ≥ 50% helmet ON at onset. Hat-off after landing does not suppress.
4. Camera clip types: `fall` only (`CAM_CLIP_TYPES`).

Do **not** also run `python -m argus_sensors` with ToF while `hat.main` / `test_all_at_once` owns bus 3.

## Commands

```bash
# Haptics / US / IMU / camera tools
python3 -m hat.tools.test_haptics check
python3 -m hat.tools.test_ultrasonic live
python3 -m hat.tools.test_us_haptics
python3 -m hat.tools.test_imu check
python3 -m hat.tools.preview_camera

# Full hat runtime (ToF wear + IMU fall + US + haptics + cam)
python3 -m hat.main
python3 -m hat.main --dry-run

# All sensors load test (includes SCD via pi_sensors subprocess)
python3 test_all_at_once.py --seconds 30

# Wear-gate unit + scripted validation
pytest hat/tests
python3 validate_wear_gated_fall.py
```

Tunables: `hat/config.py` (fall thresholds, wear gate, `CAM_PRE_S` / `CAM_POST_S`).

JSON shapes for friends: `~/argus/samples/` — see `~/argus/HANDOFF.md`.
