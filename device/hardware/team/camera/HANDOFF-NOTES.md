# Module: camera

- **Hardware:** Pi Camera on CSI; `picamera2` (apt).
- **Proven runtime:** `device/pi_bringup/hat/camera.py` — rolling buffer, dump on **fall** (20 s pre / 10 s post).
- **Teammates:** start here for integration; wire `save_command` to the real ClipRecorder when ready.
- **Quirks:** Do not open camera from two processes. Stop `hat.main` before bench camera tools.
