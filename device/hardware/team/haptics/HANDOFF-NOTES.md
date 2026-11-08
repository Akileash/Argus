# Module: haptics

- **Truth:** **2× DRV2605L** ERM discs — NOT L293D / GPIO PWM.
- LEFT 0x5A on I2C1; RIGHT 0x5A on I2C3.
- Software “top/back” motors map to **left/right** DRV boards on this hat.
- Full API: `device/pi_bringup/hat/haptics.py`
