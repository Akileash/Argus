# Hardware truth (this hat) vs older software docs

**Source of truth for the physical build:** `device/pi_bringup/` (verified on the Argus Pi).  
If this file disagrees with older notes in CONNECT-THE-HAT / mvp-spec / prior context §4, **this file and pi_bringup win**.

## Sensors we actually use

| Module | Software docs often said | **Actually built / wired** |
|--------|--------------------------|----------------------------|
| imu | MPU-6050 | MPU-6050 @ **I2C3** 0x68 |
| range | HC-SR04 R2/R3 (GPIO19/26 & 23/24 in some docs) | LEFT **Trig23/Echo24**, RIGHT **Trig20/Echo21** |
| hat_on | **VL53L0X** | **VL6180X** @ I2C3 **0x29**, near band **50–254 mm**, 5 s ON / 3 s OFF |
| env | SCD41 | SCD41 @ I2C1 **0x62** |
| haptics | **L293D** + GPIO12/13 | **2× DRV2605L** ERM @ 0x5A — LEFT **I2C1**, RIGHT **I2C3** |
| camera | Pi Camera | Pi Camera CSI / picamera2 — 20 s pre / 10 s post on fall |

## Buses (verified)

```bash
i2cdetect -y 1   # 5a LEFT DRV, 62 SCD41
i2cdetect -y 3   # 5a RIGHT DRV, 68 MPU, 29 VL6180X
```

Overlay required: `dtoverlay=i2c3,pins_4_5` in `/boot/firmware/config.txt`.

## Detection truth (pi_bringup)

- **Fall-only** (no separate live `impact` type). Hard hit or freefall path → `fall`.
- **Wear gate:** 3 s ToF lookback, ≥ 50% ON at onset before emitting fall.
- IMU sample rate proven ~**200 Hz** (software contract text asks ≥400 Hz — tune later; do not fake it).

## Do / don’t

- Do treat `samples/*.json` as the event shapes we emit today.
- Don’t reintroduce L293D/VL53 assumptions when wiring or writing drivers for this hat.
- Don’t dual-init ToF on bus 3.
