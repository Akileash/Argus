# Module: imu

- **Wiring:** Pololu MinIMU-9 v3 on **/dev/i2c-3**: L3GD20H gyro at **0x6B** (WHO_AM_I 0xD7), LSM303D accel at **0x1D** (WHO_AM_I 0x49). Needs `dtoverlay=i2c3,pins_4_5`. Replaces the MPU-6050 (0x68), damaged 2026-11-08; the old driver is archived in `device/pi_bringup/hat/archive/mpu6050.py`.
- **Libraries:** smbus2
- **Output:** JSON lines `ax ay az` in **g**, `gx gy gz` in **deg/s**, ~**200 Hz** (proven on the MPU; unverified on the MinIMU-9 until run on the Pi). Contract asks ≥400 Hz — gap documented in docs/HARDWARE-DIFF.md.
- **Quirks:** Do not also open the IMU chips from another process. Wear-gated fall lives in `device/pi_bringup/hat/`.
- **Check:** `uv run argus-cli team check imu --sample sample.txt`
