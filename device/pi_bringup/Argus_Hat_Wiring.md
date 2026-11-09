# Argus hat wiring sheet (Pi 4 — current build)

Use this when moving from breadboard to the hard hat.  
**Do not put 5V on any DRV2605L or on any GPIO.**

Code pins live in `hat/config.py` — same numbers as this sheet.

---

## Devices and addresses

| Device | Bus | Header pins (BCM) | Address | Role |
|--------|-----|-------------------|---------|------|
| **LEFT DRV2605L** | `/dev/i2c-1` | SDA pin **3** (GPIO2), SCL pin **5** (GPIO3) | **0x5A** | Left haptic |
| **SCD41** (temp/CO2) | `/dev/i2c-1` | same as LEFT DRV | **0x62** | Air via `pi_sensors/` |
| **RIGHT DRV2605L** | `/dev/i2c-3` | SDA pin **7** (GPIO4), SCL pin **29** (GPIO5) | **0x5A** | Right haptic |
| **MinIMU-9 v3** gyro (L3GD20H) | `/dev/i2c-3` | same as RIGHT DRV | **0x6B** (WHO_AM_I **0xD7**) | Wear-gated fall (gyro) |
| **MinIMU-9 v3** accel (LSM303D) | `/dev/i2c-3` | same board, same pins | **0x1D** (WHO_AM_I **0x49**) | Wear-gated fall (accel) |
| **VL6180X** ToF | `/dev/i2c-3` | same as RIGHT DRV | **0x29** | Wear gate (hat ToF) |
| **LEFT HC-SR04** | GPIO | Trig pin **16** (GPIO23), Echo pin **18** (GPIO24) via divider | — | Rear-left US |
| **RIGHT HC-SR04** | GPIO | Trig pin **38** (GPIO20), Echo pin **40** (GPIO21) via divider | — | Rear-right US |

Power: **3V3** pin 1, **5V** pin 2 (ultrasonics only), **GND** pin 6 (shared).

After wiring, expect (verified 2026-11-08):

```bash
i2cdetect -y 1   # 5a (L DRV) + 62 (SCD41)
i2cdetect -y 3   # 5a (R DRV) + 6b (gyro) + 1d (accel) + 29 (VL6180X)  [MinIMU-9 not yet verified on the Pi]
```

**Note:** Physical pins 7/29 are BCM GPIO4/GPIO5 (I2C3). Physical pins 3/5 are BCM GPIO2/GPIO3 (I2C1). `hat/config.py` has `I2C_BUS_IMU = 3`.

I2C bus 3 requires this line in `/boot/firmware/config.txt` (then reboot):

```text
dtoverlay=i2c3,pins_4_5
```

If `/dev/i2c-3` exists but bus 3 is empty, add **4.7 kΩ pull-ups** from pins 7 and 29 to 3.3V.

---

## Pi header map

Pin 1 = top-left, nearest the SD-card end. Odd pins left column, even pins right column.

```text
                         LEFT (odd)              RIGHT (even)
 3V3 → 3.3V rail          (1)  (2)   5V → 5V rail
 I2C1 SDA (GPIO2)         (3)  (4)   5V (optional / unused)
 I2C1 SCL (GPIO3)         (5)  (6)   GND → GND rail
 I2C3 SDA (GPIO4) → R-DRV (7)  (8)
 GND → R-DRV black        (9) (10)
                         (11) (12)
                         (13) (14)
                         (15) (16)   GPIO23 → LEFT US Trig
 3V3 → R-DRV red         (17) (18)   GPIO24 → LEFT US Echo (via divider)
                         (19) (20)
                         (21) (22)
                         (23) (24)
                         (25) (26)
                         (27) (28)
 I2C3 SCL (GPIO5) → R-DRV(29) (30)
                         (31) (32)
                         (33) (34)
                         (35) (36)
                         (37) (38)   GPIO20 → RIGHT US Trig
                         (39) (40)   GPIO21 → RIGHT US Echo (via divider)
```

---

## Pin-by-pin list

| Pi pin | Signal | Goes to |
|--------|--------|---------|
| **1** | 3.3V | 3.3V net: LEFT DRV red |
| **2** | 5V | 5V net: LEFT US VCC, RIGHT US VCC |
| **3** | GPIO2 / SDA1 | LEFT DRV blue |
| **5** | GPIO3 / SCL1 | LEFT DRV yellow |
| **6** | GND | GND net: LEFT DRV black, both US GND, both divider 2 kΩ |
| **7** | GPIO4 / SDA3 | RIGHT DRV blue (SDA) + **IMU SDA** |
| **9** | GND | RIGHT DRV black (GND) + **IMU GND** |
| **16** | GPIO23 | LEFT ultrasonic Trig |
| **17** | 3.3V | RIGHT DRV red (power) + **IMU VCC** |
| **18** | GPIO24 | LEFT ultrasonic Echo **after** 1k/2k divider |
| **29** | GPIO5 / SCL3 | RIGHT DRV yellow (SCL) + **IMU SCL** |
| **38** | GPIO20 | RIGHT ultrasonic Trig |
| **40** | GPIO21 | RIGHT ultrasonic Echo **after** 1k/2k divider |

Unused for this build: many mid-header pins (8, 10–15, 19–28, 30–37, 39, etc.).

---

## Power nets

### 3.3V net
- Source: Pi pin **1** (and pin **17** for RIGHT DRV + IMU)
- Loads: LEFT DRV red, RIGHT DRV red, IMU VCC
- Must measure ~**3.3 V**. If you see ~**1.2 V**, something is shorting or overloading 3V3 — fix before anything else.

### 5V net
- Source: Pi pin **2**
- Loads: both HC-SR04 VCC only
- Never feed 5V into a DRV2605L red wire or a GPIO.

### GND net
- Source: Pi pin **6** (and pin **9** for RIGHT DRV + IMU)
- Everything shares ground: IMU, both DRVs, both ultrasonics, both divider 2 kΩ legs.

---

## STEMMA QT cable (DRV2605L boards)

| Wire colour | Signal | LEFT board | RIGHT board |
|-------------|--------|------------|-------------|
| **Red** | 3.3V | 3.3V net (Pi pin 1) | Pi pin **17** |
| **Black** | GND | GND net (Pi pin 6) | Pi pin **9** |
| **Blue** | SDA | Pi pin **3** | Pi pin **7** (shared with IMU SDA) |
| **Yellow** | SCL | Pi pin **5** | Pi pin **29** (shared with IMU SCL) |

- Motor wires go on each board’s **motor pads** (`+` / `-`). Polarity does not matter for ERM discs.
- Leave EN / IN / INT unconnected.
- Both DRV chips are address **0x5A**, so they **must** stay on separate I2C buses (1 vs 3).

---

## Ultrasonic echo dividers (required)

HC-SR04 Echo is **5 V**. Pi GPIO is **3.3 V**. Never wire Echo straight to a GPIO.

```text
LEFT:   Echo --[1 kΩ]--+-- Pi pin 18 (GPIO24)
                       |
                     [2 kΩ]
                       |
                      GND

RIGHT:  Echo --[1 kΩ]--+-- Pi pin 40 (GPIO21)
                       |
                     [2 kΩ]
                       |
                      GND
```

| Sensor | Trig | Echo (after divider) | VCC | GND |
|--------|------|----------------------|-----|-----|
| LEFT | GPIO23 (pin 16) | GPIO24 (pin 18) | 5V | GND |
| RIGHT | GPIO20 (pin 38) | GPIO21 (pin 40) | 5V | GND |

Trig goes **straight** to the Pi (no divider).

---

## GPIO / I2C summary for code

```text
LEFT ultrasonic:   Trig 23, Echo 24
RIGHT ultrasonic:  Trig 20, Echo 21
LEFT haptic:       I2C bus 1, address 0x5A
RIGHT haptic:      I2C bus 3, address 0x5A
MinIMU-9 v3:       I2C bus 3, gyro 0x6B + accel/mag 0x1D
```

---

## Suggested hard-hat layout

| Part | Mount idea |
|------|------------|
| Pi + cooler + power bank | Rear / earmuff-side enclosure |
| LEFT + RIGHT HC-SR04 | Brim rear, angled in a **V** looking backward |
| MinIMU-9 | Rigid to hat shell (fixed orientation) |
| LEFT DRV + motor | Left side of shell / padding |
| RIGHT DRV + motor | Right side of shell / padding |
| Dividers | Heat-shrink or tiny proto next to each sensor |

Keep I2C wires short. Strain-relieve every cable so bumps don’t yank headers.

---

## Pre-power checklist

- [ ] RIGHT DRV: blue→7, yellow→29, red→17, black→9
- [ ] LEFT DRV: blue/yellow on pins 3/5, red→3.3V, black→GND
- [ ] No DRV red wire on 5V
- [ ] IMU VCC on 3.3V (pin 17 ok), GND with RIGHT DRV, SDA/SCL on pins 7/29 (I2C3)
- [ ] Each US Echo through its **own** 1k into its **own** junction with its **own** 2k to GND
- [ ] Both US VCC on 5V, GND on GND
- [ ] Measure 3.3V rail ≈ 3.3V before trusting haptics
- [ ] `dtoverlay=i2c3,pins_4_5` enabled

---

## Bring-up commands

```bash
cd ~/argus

i2cdetect -y 1
i2cdetect -y 3

python3 -m hat.tools.test_imu check
python3 -m hat.tools.test_ultrasonic live
python3 -m hat.tools.test_haptics left
python3 -m hat.tools.test_haptics right

python3 -m hat.main
```

---

## Notes from bring-up (hackathon)

- IMU: expect WHO_AM_I gyro **0xD7** @0x6B and accel **0x49** @0x1D (`python -m hat.tools.test_imu check`). The old MPU-6050 (0x68 / WHO_AM_I 0x70) was damaged 2026-11-08 and is archived.
- If LEFT never shows `5a` on bus 1, recheck LEFT STEMMA power/SDA/SCL — software cannot buzz a missing board.
- If RIGHT shows `5a` but the disc does not vibrate, check motor wires on the **motor pads** (chip can be fine while the motor is disconnected/dead).
- 3.3V reading ~1.2V means a power fault — fix rail before debugging code.
