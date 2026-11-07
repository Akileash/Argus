# From a blank SD card to your first real incident report

This guide takes you from an empty SD card to a real event on the dashboard. Follow the steps in
order. After each step there is a short "You should see" note. If you do not see it, read the
"If you don't" note under it.

Two rules for the whole guide:

- **Power off the Pi before you change any wiring.** Turn it off with `sudo poweroff`, wait for the
  green light to stop, then unplug it.
- **Stop the service before you run any bench command.** The bench commands and the service both
  open the same sensors and motor pins. Two programs cannot use them at once.

```bash
sudo systemctl stop argus
```

Commands that start with `$` are typed on your laptop or on the Pi, as noted. Do not type the `$`.

---

## Before you start

You need:

- A Raspberry Pi 4 with its power supply (5 V, 3 A).
- A microSD card (8 GB or larger) and a card reader.
- A laptop with Wi-Fi, and Raspberry Pi Imager installed.
- The hat wired up as in the spec (`.plans/argus-mvp-spec.md`, section 2).
- Access to the Argus dashboard, with a **manager** login. Only managers can register devices.
- The repo cloned onto your laptop (you will clone it again onto the Pi in step (b)).

---

## (a) Flash the SD card

1. Open **Raspberry Pi Imager** on your laptop.
2. Click **Choose Device** and pick **Raspberry Pi 4**.
3. Click **Choose OS**. Go to **Raspberry Pi OS (other)** and pick
   **Raspberry Pi OS Lite (64-bit)**.

   > Pick the release named **Bookworm**. The install script expects Bookworm (Python 3.11 and
   > `/boot/firmware/config.txt`). If only a newer release (Trixie) is offered, stop and ask the PM.

4. Click **Choose Storage** and pick your SD card. Check the name twice. Everything on the card is
   erased.
5. Click **Next**. When asked about customisation, click **Edit Settings** and fill in:
   - **Hostname:** `argus-01` (or any name you like, but use it below as `<hostname>`)
   - **Username and password:** write these down. The install script runs as this user.
   - **Wi-Fi:** your network name, password and country.
   - **Services tab:** turn on **Enable SSH** and choose password login (or add your public key).
6. Click **Save**, then **Yes**, then wait for the write and verify steps to finish.
7. Put the card in the Pi. Power it on with the 5 V supply.

**You should see:** after about 1 to 2 minutes, the Pi shows up on your network under the name
`<hostname>.local`.

**If you don't:**
- Wait 3 minutes. The first boot takes longer than later ones.
- Check that the Wi-Fi name and password are right. A typo here is the most common cause.
- Check your router's device list for an address called `<hostname>`.
- Check that the SD card is seated fully in the slot.

---

## (b) SSH in, clone the repo, run the installer

### b1. Log in

On your laptop:

```bash
$ ssh <username>@<hostname>.local
```

The first time, type `yes` when asked about the host key. Then type the password you set in Imager.

Check the OS and the hardware:

```bash
uname -m
grep VERSION_CODENAME /etc/os-release
cat /proc/device-tree/model; echo
```

**You should see:**
- `aarch64`
- `VERSION_CODENAME=bookworm`
- a line that contains `Raspberry Pi 4`

**If you don't:** if the codename is not `bookworm`, stop and ask the PM before you go on. The
installer refuses to run on other systems.

### b2. Clone the repo

> The repo has no git remote yet. Someone needs to push it to GitHub (or similar) first, then use that
> URL as `<REPO-URL>` below.

```bash
git clone <REPO-URL> ~/argus
cd ~/argus
```

**You should see:** a folder called `~/argus` that contains `device/`, `web/` and `contracts/`.

### b3. Run the installer

Run the installer as your normal user, **not** with `sudo`. It asks for your password when it needs
`sudo`.

```bash
cd ~/argus
./device/install.sh
```

The installer takes several minutes. It installs system packages, enables I2C, turns on the camera
and the motor PWM, creates the Python environment, and sets up the `argus` service.

It ends with a checklist. The first run usually ends like this:

```
REBOOT REQUIRED. I2C, camera and PWM boot settings changed.
  1. sudo reboot
```

and later:

```
device is not paired yet, so the service was NOT started.
```

**You should see:** `Argus device install finished.` near the end.

**If you don't:**
- `this must run on a Raspberry Pi` → you are not on the Pi, or the OS is not 64-bit.
- `do not run as root` → log out, log in as your normal user, and run it again without `sudo`.
- An error from `apt-get` → run the same command again. The installer is safe to re-run.

### b4. Reboot, then run the installer again

```bash
sudo reboot
```

Wait 1 minute, then log in again with `ssh` and run the installer a second time:

```bash
cd ~/argus
./device/install.sh
```

**You should see:** no `REBOOT REQUIRED` line this time. The last lines say the service was **not**
started, because the device is not paired yet. That is correct.

Check that the I2C bus exists:

```bash
ls -l /dev/i2c-1
```

**You should see:** `/dev/i2c-1` in the output.

**If you don't:** I2C is still off. Run `sudo raspi-config nonint do_i2c 0`, then `sudo reboot`.

### b5. Check the installed config file

The service does not read the repo's `config.yaml`. It reads a copy that the installer put in
`/etc/argus/config.yaml`. **Every module in that copy is set to `mock`.** You will change it in
step (d).

```bash
cat /etc/argus/config.yaml | sed -n 1,12p
```

**You should see:** `impl: mock` on each line.

---

## (c) Wiring check

### c1. The GPIO table

Use this table to check your wiring. It is copied from spec section 2.3 (Pi 4, BCM numbering).

| Function | Pins |
|---|---|
| I2C bus (IMU, VL53L0X, SCD41) | GPIO2 (SDA), GPIO3 (SCL) |
| Motor top (EN1, PWM) | GPIO12 (hardware PWM0) |
| Motor back (EN2, PWM) | GPIO13 (hardware PWM1) |
| HC-SR04 R2 (7.5° left of straight back) TRIG / ECHO | GPIO19 / GPIO26 |
| HC-SR04 R3 (7.5° right of straight back) TRIG / ECHO | GPIO23 / GPIO24 |
| Reserved for more sensors later | R1 GPIO5 / GPIO6, R4 GPIO16 / GPIO25, TOP GPIO20 / GPIO21 |
| Status LED (optional) | GPIO17 |

Other wiring rules from the spec:

- **HC-SR04 ECHO** outputs 5 V. Put a divider (1 kΩ / 2 kΩ, about 3.3 V) between ECHO and the Pi
  pin. A direct connection can damage the Pi.
- **Each HC-SR04 gets its own TRIG pin.** Do not share TRIG.
- **MPU-6050:** tie AD0 to GND, so the address is 0x68.
- **L293D:** Vcc1 (pin 16) is 5 V logic. Vcc2 (pin 8) is the 5 V motor supply. Connect grounds
  together. Put a 100 µF and a 0.1 µF capacitor across Vcc2.
- **Pi Camera:** seat the ribbon cable fully and close the latch on the connector.

### c2. Check the I2C bus

```bash
i2cdetect -y 1
```

**You should see** a grid. Three addresses must appear:

```
     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f
20: -- -- -- -- -- -- -- -- -- 29 -- -- -- -- -- --
...
60: -- -- 62 -- -- -- -- -- 68 -- -- -- -- -- -- --
```

| Address | Device |
|---|---|
| `0x29` | VL53L0X (hat-on sensor) |
| `0x62` | SCD41 (CO2, temperature, humidity) |
| `0x68` | MPU-6050 (IMU) |

**If you don't:**
- **The grid is empty:** I2C is off, or the bus is not wired. See the troubleshooting table, "No I2C device".
- **`0x69` appears instead of `0x68`:** AD0 on the MPU-6050 is not tied to GND.
- **`Permission denied`:** the installer added you to the `i2c` group, but the change needs a new
  login. Log out, log in, and try again.
- **One address is missing:** check that sensor's wiring and power, then run `i2cdetect -y 1` again.

### c3. Check the camera

```bash
rpicam-hello --list-cameras
```

On an older image the command is `libcamera-hello --list-cameras`.

**You should see:** one camera listed, with a sensor name (IMX219 for the Pi Cam V2.1).

**If you don't:** see "Camera not detected" in the troubleshooting table.

---

## (d) Switch the modules from `mock` to real hardware, one at a time

Do this for each module, in this order: `imu`, `range`, `hat_on`, `env`, `haptics`, `camera`.
For each one:

1. Edit `/etc/argus/config.yaml`. Change the `impl` for that module from `mock` to `argus`.
   You do not need `sudo`: the file belongs to your user.

   ```bash
   nano /etc/argus/config.yaml
   ```

   For example, change this line:

   ```yaml
     imu:     { impl: mock }
   ```

   to this:

   ```yaml
     imu:     { impl: argus }
   ```

   Save with `Ctrl+O`, then `Enter`, then exit with `Ctrl+X`.

   > Use `team` instead of `argus` if the team's script is the one you want for that module. A `team`
   > module does nothing until the team's script is pasted in. See `docs/TEAM-HANDOFF.md`.

2. Run the bench test for that module. Pass the config so the command reads the same file:

   ```bash
   cd ~/argus/device
   .venv/bin/argus-cli test imu --config /etc/argus/config.yaml
   ```

   Replace `imu` with the module name each time.

3. Expected for each module (from `device/hardware/argus/README.md`):

| Module | What the test checks | You should see |
|---|---|---|
| `imu` | accelerometer, sample rate, error count | `PASS` lines. Accel z about +1 g when the hat is flat. Rate about 1000 Hz. |
| `range` | two sensors, sweep order | R2 and R3 both report every ~60 ms. A flat target at 0.5, 1.0 and 2.0 m reads within ±1 cm. |
| `hat_on` | on-head reading | about 50 to 80 mm with the hat on your head. `out_of_range` with the hat off. |
| `env` | first CO2 reading | first sample after about 5 s. CO2 about 400 to 1000 ppm. |
| `haptics` | motor drive | `argus-cli buzz both` makes both motors buzz (see step 4). |
| `camera` | 720p recording and clip save | a clip saved. `ffprobe` shows H.264, 1280×720, about 30 fps. |

4. Buzz the motors (for `haptics`):

   ```bash
   .venv/bin/argus-cli buzz both --config /etc/argus/config.yaml
   ```

   **You should see:** `buzzed both at 100% for 0.6s`, and both motors vibrate for 0.6 seconds.

**If you don't:**
- **`FAIL` on a check:** read the line above it. The bench test prints what it expected and what it got.
- **A Python error about a missing library (for example `smbus2`, `lgpio` or `adafruit`):** the Pi
  libraries are missing. Run `cd ~/argus/device && uv sync --locked --extra pi`, then test again.
- **A GPIO or I2C "busy" or "permission" error:** the service is probably still running. Run
  `sudo systemctl stop argus`, then test again.
- **Motors silent:** see "Motors silent" in the troubleshooting table.

Keep the change only if the test passes. If a module fails and you want the mock back for now, set
its `impl` back to `mock`.

When all modules that you want are switched, check the file once more:

```bash
sed -n 1,12p /etc/argus/config.yaml
```

**You should see:** `impl: argus` (or `team`) on each module you switched.

---

## (e) Pair the hat with the backend

### e0. Choose an address the hat can reach

The hat is a separate computer, so `localhost` and `127.0.0.1` will **not** work from it. Pick one:

**Option A: dashboard on your laptop (local Supabase).** The Pi and laptop must be on the same Wi-Fi.

1. Find the laptop's IP address on your Mac: `ipconfig getifaddr en0`
2. In `web/.env.local`, set `NEXT_PUBLIC_SUPABASE_URL=http://<laptop IP>:54321`.
   **This step is easy to miss.** Hats upload to addresses built from this setting. If it still says
   `127.0.0.1`, pairing works but every upload fails.
3. Restart the dashboard (rebuild first if you use `pnpm build`), and sign in again.
4. Your `<dashboard URL>` is `http://<laptop IP>:<port>`. Allow incoming connections if macOS asks.
5. The laptop's IP can change between networks. If it does, repeat steps 2 and 3, then pair again.

**Option B: hosted (Vercel + hosted Supabase).** Your `<dashboard URL>` is the `https://` address.
Nothing else is needed, and it works on any network the Pi can get online from.

### e1. Register the device in the dashboard

1. Sign in to the dashboard with a **manager** account.
2. Click **Devices** in the side menu.
3. On the right, under **Register device** → **Pair a hat**, type the hat's serial in the
   **Serial** box, for example `ARG-0001`.
4. Leave **Issue a new token if already paired** unticked for a new device. Tick it only if you
   are pairing a hat that was paired before (see "401 after re-pair" in the troubleshooting table).
5. Click **Register device**.
6. The page shows **Device `<serial>` paired** and the **device token**.
   **Click Copy now.** The token is shown only once. If you lose it, register the device again with
   the box ticked.

### e2. Pair the Pi

On the Pi:

```bash
cd ~/argus/device
.venv/bin/argus-cli pair --url <dashboard URL> --token <token> --config /etc/argus/config.yaml
```

- Replace `<dashboard URL>` with the dashboard's address, for example `https://argus.example.com`.
  Do not add `/api` or a trailing slash.
- Replace `<token>` with the token you copied.
- Tip: leave out `--token <token>` and the command asks for the token with hidden typing. This keeps
  the token out of your shell history.
- `--config /etc/argus/config.yaml` is needed. Without it the command writes to `device/config.yaml`
  in the repo, and the service does not read that file.

**You should see:**

```
paired; wrote /etc/argus/config.yaml
device ARG-0001 (org ...), assigned worker: (no worker assigned)
a running `argus-cli run` notices the new token within ~10 s
```

`(no worker assigned)` is normal until a worker is assigned to the hat in the dashboard.

**If you don't:**
- `cannot reach ...` → the Pi cannot reach the dashboard. Check the URL and the Pi's Wi-Fi.
- `backend rejected the device token (401)` → the token is wrong. Copy it again, or register with the
  box ticked. Nothing is written when pairing fails.

### e3. Check the pairing

```bash
.venv/bin/argus-cli pair --check --config /etc/argus/config.yaml
```

**You should see:** `OK device ARG-0001 (org ...), assigned worker: ...`

**If you don't:** `not paired` means the first command did not write the file. Run it again with the
same `--config` value.

---

## (f) Start the service

```bash
sudo systemctl start argus
systemctl status argus --no-pager
```

**You should see:** `Active: active (running)`.

Watch the live log:

```bash
journalctl -u argus -f
```

Stop watching with `Ctrl+C`. The service keeps running.

**You should see**, about once a second:

```
argus running (scenario=idle); Ctrl-C to stop
buffered {'imu': ..., 'range': ..., 'tof': ..., 'env': ...} impls {'imu': 'argus', 'range': 'argus', ...}
```

Check that `impls` shows `argus` (or `team`) for every module you switched. If a module shows `mock`,
step (d) did not save.

Check the upload queue:

```bash
.venv/bin/argus-cli upload --status --config /etc/argus/config.yaml
```

**You should see:** one status line like this:

```
paired=yes queue_depth=0 pending=0 rejected=0 oldest_age=- last_error=-
```

A new device with no events has an empty queue (`queue_depth=0`).

**If you don't:**
- **`activating` then `failed`, or the service keeps restarting:** run `journalctl -u argus -n 100 --no-pager`
  and read the last error. A `module_fallback` line means one module failed and switched or stopped.
- **`EVENT {"type": "module_fallback", ...}` in the log:** that module failed. Read its `reason`, fix
  it, and run `.venv/bin/argus-cli reload <module>`.
- **Still `mock` in `impls`:** the module is on `mock` in `/etc/argus/config.yaml`. Fix it, then run
  `sudo systemctl restart argus`.

---

## (g) Your first real event

1. Leave the hat still on a table for about 10 seconds. The detectors calibrate after the service starts.
2. Open the dashboard and go to **Incidents**. Leave the page open.
3. Put a soft mat on the floor, next to the table.
4. Hold the hat about 1 m above the mat. Drop it.
5. Watch the log on the Pi:

   ```bash
   journalctl -u argus -f | grep EVENT
   ```

**You should see:**
- within a few seconds, a line like `EVENT {"type": "fall", ...}` or `EVENT {"type": "head_impact", ...}`;
- on the dashboard, a new row at the top of **Incidents**;
- a toast in the corner of the page. It says **Report generating…**, then **Report ready** (or
  **Report failed**). Click the toast to open the incident;
- on the incident page, the report and the sensor data. A `hat_removed` event does not get a report,
  so it goes straight to **ready**.

Optional second test: take the hat off and put it on the table. Expect a `hat_removed` event within
about 3 seconds.

**If you don't:**
- **No `EVENT` line:** check that the impls show `argus`, not `mock` (step f). Drop from a higher
  height, or onto a harder surface, if the fall is too soft.
- **`EVENT` shows but nothing on the dashboard:** the device is not paired, or the uploads are
  stuck. Run `.venv/bin/argus-cli upload --status --config /etc/argus/config.yaml`. See "Uploads
  stuck" below.
- **Toast says `Report failed`:** click the toast. The incident page shows what went wrong. Tell the
  PM the incident ID.

> **Stage backup, not a test:** `.venv/bin/argus-cli trigger fall --config /etc/argus/config.yaml` creates
> an event with a real clip without any sensor firing. Use it if the sensors misbehave during the pitch.
> It proves nothing about the detectors, so always do the real drop here.

---

## (h) Recording and tuning

Tuning turns real recordings into better thresholds. The full guide is `docs/TUNING.md`. Read it
before you tune anything. These are the three commands you will use most, run from `~/argus/device`:

```bash
# 1. Score the detectors against labelled recordings.
.venv/bin/argus-cli eval --recordings recordings/

# 2. Try a grid of threshold values and print the best.
.venv/bin/argus-cli sweep --param struck_by.ego_tau_s=1.0,1.5,2.0 --recordings recordings/

# 3. Write the mock scenarios as recordings, to practise without hardware.
.venv/bin/argus-cli export-scenarios --out recordings/scenarios
```

- `eval` expects a folder with `.jsonl` recordings and a `.labels.json` file next to each one.
  Recordings with no label file are skipped, with a warning.
- `sweep --apply` writes the best values into the config. Review with `git diff` first.
- The default recordings folder is `device/recordings`. It does not exist on a new install. Create it,
  or pass `--recordings`.

To record a labelled run, use `argus-cli record`. Stop the service first, because it needs the sensors:

```bash
sudo systemctl stop argus
.venv/bin/argus-cli record --label board_swing_rear --seconds 30 \
  --expect struck_by_near_miss --config /etc/argus/config.yaml
```

This writes a `.jsonl` recording and a `.labels.json` file to `device/recordings/`.

---

## (i) Troubleshooting

| Symptom | Check first | Fix |
|---|---|---|
| **No I2C device**: `i2cdetect -y 1` is empty, or an address is missing | `ls -l /dev/i2c-1`. If it is missing, I2C is off. | `sudo raspi-config nonint do_i2c 0`, then `sudo reboot`. Check SDA (GPIO2) and SCL (GPIO3), power and ground on the sensor. For the MPU-6050, tie AD0 to GND so the address is `0x68`. Run `i2cdetect -y 1` again. |
| **Camera not detected**: `rpicam-hello --list-cameras` shows nothing | `grep camera_auto_detect /boot/firmware/config.txt`. It should say `camera_auto_detect=1`. | Power off, reseat the ribbon cable, and close the latch. Then power on. If the flag is `0`, change it to `1` and reboot. |
| **Motors silent**: `argus-cli buzz both` gives no vibration | The service is stopped (`systemctl status argus`). If it is running, the pins are in use. | Check GPIO12 (top) and GPIO13 (back) go to the L293D enable pins. Check Vcc2 has 5 V and grounds are joined. Check the 100 µF capacitor. If the motors only stall at low strength, raise `min_duty` for `haptics` in `/etc/argus/config.yaml`. |
| **Uploads stuck**: events do not reach the dashboard | `.venv/bin/argus-cli upload --status --config /etc/argus/config.yaml`. Read `queue_depth`, `pending`, `rejected` and `last_error`. | If `last_error` says a network problem, check the Pi's Wi-Fi and the dashboard URL. To push the queue once, stop the service and run `.venv/bin/argus-cli upload --once --config /etc/argus/config.yaml`. It exits with 0 when nothing is pending or rejected. Start the service again after. |
| **401 after re-pair**: uploads say `401 unauthorized` | The device token in `/etc/argus/config.yaml`. | Register the device again in the dashboard with **Issue a new token if already paired** ticked. Copy the new token. Run `.venv/bin/argus-cli pair --url <dashboard URL> --config /etc/argus/config.yaml` and paste the token. A running service notices the new token within about 10 s. Check with `pair --check`. |
| **Under-voltage**: the Pi slows down or restarts when the motors buzz | `vcgencmd get_throttled` | `throttled=0x0` is good. Any other value means the power is dropping. Use a supply that gives 5 V and 3 A. The Anker power bank is for demos only (5 V, 3 A max). Run the motors and sensors together for 10 minutes and check again. |
| Service keeps restarting | `journalctl -u argus -n 100 --no-pager` | Read the last error and fix that first. |
| `module_fallback` in the log | The `reason` in the event line | Fix the module that failed, then `.venv/bin/argus-cli reload <module>`. Fallbacks do not switch back by themselves. |

### Useful commands for every problem

```bash
systemctl status argus --no-pager          # is the service running?
journalctl -u argus -n 100 --no-pager      # last 100 log lines
journalctl -u argus -f                     # live log (Ctrl+C to stop)
vcgencmd get_throttled                     # power problems
.venv/bin/argus-cli upload --status --config /etc/argus/config.yaml
```

Run `.venv/bin/argus-cli` from `~/argus/device`. The `.venv` folder is made by the installer.
