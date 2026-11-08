# Module: imu

- **Wiring:** MPU-6050 on **/dev/i2c-3**, addr **0x68** (WHO_AM_I may be 0x70). Needs `dtoverlay=i2c3,pins_4_5`.
- **Libraries:** smbus2
- **Output:** JSON lines `ax ay az` in **g**, `gx gy gz` in **deg/s**, ~**200 Hz** (proven). Contract asks ≥400 Hz — gap documented in docs/HARDWARE-DIFF.md.
- **Quirks:** Do not also open MPU from another process. Wear-gated fall lives in `device/pi_bringup/hat/`.
- **Check:** `uv run argus-cli team check imu --sample sample.txt`
