# Module: hat_on

- **Sensor truth:** **VL6180X** @ I2C3 **0x29** (not VL53L0X).
- **Wear band:** near = 50–254 mm; ON dwell 5 s, OFF dwell 3 s (see pi_bringup/hat/config.py).
- **Do not** dual-run with `python -m argus_sensors` ToF while hat owns bus 3.
- Full wear-gated fall uses `pi_bringup/hat/tof.py` + `wear_state.py`.
