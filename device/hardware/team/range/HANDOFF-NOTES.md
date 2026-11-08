# Module: range

- **Wiring:** LEFT Trig GPIO23 / Echo GPIO24; RIGHT Trig GPIO20 / Echo GPIO21 (1k/2k divider on echo).
- **IDs:** back_left → R2, back_right → R3.
- **Output:** JSON `dist_cm`; `0` = no echo.
- **Quirks:** Empty cone → 0. Dual ping gap ~40 ms in full hat stack.
