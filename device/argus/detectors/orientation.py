"""6-axis orientation (Madgwick, no magnetometer), gravity removal, boot calibration.

Spec: §2.6 (MPU-6050, no magnetometer -> yaw drifts, pitch/roll well observed),
§4.1 step 3, §4.3 boot sequence (calibration at boot only, restart on motion).

Frames
------
- Hat frame: x forward, y left, z up (contracts.py). Accel in g, gyro in deg/s.
- World ("level") frame: z = up (opposite gravity); x/y horizontal with an
  arbitrary, slowly drifting heading. Only pitch/roll are meaningful.

The quaternion ``q = (w, x, y, z)`` rotates hat-frame vectors into the world
frame: ``v_world = q * v_hat * q^-1``. The accelerometer measures specific
force, so a still, level hat reads (0, 0, +1 g).

Pure Python floats on purpose: per-sample cost must stay well under 1 ms on a
Pi and numpy's per-call overhead dominates for 3-vectors.

cfg keys (all optional, from thresholds.struck_by):
    madgwick_beta (0.01)        gradient-descent gain, rad/s (low: sustained horizontal accel
                                must not be mistaken for tilt; gyro bias is calibrated at boot)
    accel_gate_g (0.25)         skip accel correction when | |a| - g | exceeds this
    max_dt_s (0.05)             clamp gyro integration step across sample gaps
    calib_window_s (2.0)        still window length for boot calibration (§4.3)
    calib_gyro_motion_dps (3.0) motion if any gyro axis deviates this much from the window mean
    calib_accel_motion_g (0.05) motion if |a| deviates this much from the window mean
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from argus.contracts import ImuSample

G0 = 9.80665  # m/s^2 per g
DEG = math.pi / 180.0

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]


# ---------------------------------------------------------------- quaternion helpers
def quat_normalize(q: Quat) -> Quat:
    n = math.sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3])
    if n == 0.0:
        return (1.0, 0.0, 0.0, 0.0)
    return (q[0] / n, q[1] / n, q[2] / n, q[3] / n)


def quat_rotate(q: Quat, v: Vec3) -> Vec3:
    """Rotate v by q (hat -> world for the filter quaternion)."""
    w, x, y, z = q
    vx, vy, vz = v
    # t = 2 * cross(q.xyz, v); v' = v + w*t + cross(q.xyz, t)
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    return (
        vx + w * tx + (y * tz - z * ty),
        vy + w * ty + (z * tx - x * tz),
        vz + w * tz + (x * ty - y * tx),
    )


def quat_rotate_inv(q: Quat, v: Vec3) -> Vec3:
    """Rotate v by q^-1 (world -> hat)."""
    return quat_rotate((q[0], -q[1], -q[2], -q[3]), v)


def quat_from_gravity(a: Vec3) -> Quat:
    """Shortest-arc quaternion taking the measured 'up' (accel direction) to world +z.

    Heading is arbitrary (no magnetometer); this choice gives zero yaw twist.
    """
    n = math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])
    if n == 0.0:
        return (1.0, 0.0, 0.0, 0.0)
    ax, ay, az = a[0] / n, a[1] / n, a[2] / n
    d = az  # dot(a, z)
    if d < -0.999999:  # upside down: rotate 180 deg about x
        return (0.0, 1.0, 0.0, 0.0)
    # cross(a, z) = (ay, -ax, 0)
    return quat_normalize((1.0 + d, ay, -ax, 0.0))


def tilt_deg_of(q: Quat) -> float:
    """Angle between hat +z and world up."""
    up = quat_rotate(q, (0.0, 0.0, 1.0))
    return math.degrees(math.acos(max(-1.0, min(1.0, up[2]))))


def roll_pitch_deg(q: Quat) -> tuple[float, float]:
    """(roll, pitch) in degrees, from the up direction seen in the hat frame.

    Heading-independent. Pitch > 0: nose (+x) up. Roll > 0: left side (+y) up.
    """
    g = quat_rotate_inv(q, (0.0, 0.0, 1.0))  # world up expressed in hat frame
    roll = math.degrees(math.atan2(g[1], g[2]))
    pitch = math.degrees(math.atan2(g[0], math.sqrt(g[1] * g[1] + g[2] * g[2])))
    return roll, pitch


# ---------------------------------------------------------------- boot calibration
@dataclass(frozen=True, slots=True)
class Calibration:
    gyro_bias_dps: Vec3
    gravity_hat_g: Vec3  # mean accel during the still window (hat frame)
    g_norm: float  # |mean accel| in g (absorbs accel scale error)
    t_ns: int  # end of the still window
    n_samples: int
    restarts: int


class BootCalibrator:
    """Gyro bias + gravity from a still window (spec §4.3 step 1).

    Feed IMU samples; returns a Calibration once ``calib_window_s`` of
    uninterrupted stillness has been seen. Any motion restarts the window.
    """

    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.window_ns = int(float(cfg.get("calib_window_s", 2.0)) * 1e9)
        self.gyro_motion = float(cfg.get("calib_gyro_motion_dps", 3.0))
        self.accel_motion = float(cfg.get("calib_accel_motion_g", 0.05))
        self.restarts = 0
        self.result: Calibration | None = None
        self._reset()

    def _reset(self) -> None:
        self._t0: int | None = None
        self._n = 0
        self._sa = [0.0, 0.0, 0.0]
        self._sg = [0.0, 0.0, 0.0]
        self._san = 0.0

    def feed(self, s: ImuSample) -> Calibration | None:
        if self.result is not None:
            return self.result
        an = math.sqrt(s.ax * s.ax + s.ay * s.ay + s.az * s.az)
        if self._n >= 10:
            n = self._n
            moving = (
                abs(s.gx - self._sg[0] / n) > self.gyro_motion
                or abs(s.gy - self._sg[1] / n) > self.gyro_motion
                or abs(s.gz - self._sg[2] / n) > self.gyro_motion
                or abs(an - self._san / n) > self.accel_motion
            )
            if moving:
                self.restarts += 1
                self._reset()
        if self._t0 is None:
            self._t0 = s.t_ns
        self._n += 1
        self._sa[0] += s.ax
        self._sa[1] += s.ay
        self._sa[2] += s.az
        self._sg[0] += s.gx
        self._sg[1] += s.gy
        self._sg[2] += s.gz
        self._san += an
        if s.t_ns - self._t0 >= self.window_ns and self._n >= 10:
            n = self._n
            ga = (self._sa[0] / n, self._sa[1] / n, self._sa[2] / n)
            self.result = Calibration(
                gyro_bias_dps=(self._sg[0] / n, self._sg[1] / n, self._sg[2] / n),
                gravity_hat_g=ga,
                g_norm=math.sqrt(ga[0] ** 2 + ga[1] ** 2 + ga[2] ** 2),
                t_ns=s.t_ns,
                n_samples=n,
                restarts=self.restarts,
            )
        return self.result


# ---------------------------------------------------------------- Madgwick filter
class OrientationFilter:
    """6-axis Madgwick AHRS (IMU variant) plus gravity removal."""

    def __init__(self, cfg: dict | None = None, calibration: Calibration | None = None):
        cfg = cfg or {}
        self.beta = float(cfg.get("madgwick_beta", 0.01))
        self.accel_gate_g = float(cfg.get("accel_gate_g", 0.25))
        self.max_dt_s = float(cfg.get("max_dt_s", 0.05))
        self.q: Quat = (1.0, 0.0, 0.0, 0.0)
        self.gyro_bias: Vec3 = (0.0, 0.0, 0.0)
        self.g_norm = 1.0
        self.last_t_ns: int | None = None
        # outputs of the latest update
        self.omega_dps: Vec3 = (0.0, 0.0, 0.0)
        self.omega_norm_dps = 0.0
        self.accel_norm_g = 0.0
        self.lin_acc_world: Vec3 = (0.0, 0.0, 0.0)  # m/s^2
        self.lin_acc_hat: Vec3 = (0.0, 0.0, 0.0)  # m/s^2
        if calibration is not None:
            self.apply_calibration(calibration)

    def apply_calibration(self, cal: Calibration) -> None:
        self.gyro_bias = cal.gyro_bias_dps
        self.g_norm = cal.g_norm if cal.g_norm > 0.5 else 1.0
        self.q = quat_from_gravity(cal.gravity_hat_g)
        self.last_t_ns = cal.t_ns

    def reset(self, q: Quat = (1.0, 0.0, 0.0, 0.0)) -> None:
        self.q = quat_normalize(q)
        self.last_t_ns = None

    # -- core
    def update(self, s: ImuSample) -> None:
        dt = 0.0 if self.last_t_ns is None else (s.t_ns - self.last_t_ns) * 1e-9
        self.last_t_ns = s.t_ns
        if dt < 0.0:
            dt = 0.0
        elif dt > self.max_dt_s:
            dt = self.max_dt_s
        gxd = s.gx - self.gyro_bias[0]
        gyd = s.gy - self.gyro_bias[1]
        gzd = s.gz - self.gyro_bias[2]
        self.omega_dps = (gxd, gyd, gzd)
        self.omega_norm_dps = math.sqrt(gxd * gxd + gyd * gyd + gzd * gzd)
        self._madgwick(gxd * DEG, gyd * DEG, gzd * DEG, s.ax, s.ay, s.az, dt)

        # gravity removal
        ax, ay, az = s.ax, s.ay, s.az
        self.accel_norm_g = math.sqrt(ax * ax + ay * ay + az * az)
        fw = quat_rotate(self.q, (ax, ay, az))
        lw = (fw[0] * G0, fw[1] * G0, (fw[2] - self.g_norm) * G0)
        self.lin_acc_world = lw
        self.lin_acc_hat = quat_rotate_inv(self.q, lw)

    def _madgwick(self, gx: float, gy: float, gz: float, ax: float, ay: float, az: float, dt: float) -> None:
        q0, q1, q2, q3 = self.q
        qd0 = 0.5 * (-q1 * gx - q2 * gy - q3 * gz)
        qd1 = 0.5 * (q0 * gx + q2 * gz - q3 * gy)
        qd2 = 0.5 * (q0 * gy - q1 * gz + q3 * gx)
        qd3 = 0.5 * (q0 * gz + q1 * gy - q2 * gx)
        an = math.sqrt(ax * ax + ay * ay + az * az)
        if an > 0.0 and abs(an - self.g_norm) <= self.accel_gate_g:
            ax, ay, az = ax / an, ay / an, az / an
            _2q0, _2q1, _2q2, _2q3 = 2 * q0, 2 * q1, 2 * q2, 2 * q3
            _4q0, _4q1, _4q2 = 4 * q0, 4 * q1, 4 * q2
            _8q1, _8q2 = 8 * q1, 8 * q2
            q0q0, q1q1, q2q2, q3q3 = q0 * q0, q1 * q1, q2 * q2, q3 * q3
            s0 = _4q0 * q2q2 + _2q2 * ax + _4q0 * q1q1 - _2q1 * ay
            s1 = (
                _4q1 * q3q3
                - _2q3 * ax
                + 4 * q0q0 * q1
                - _2q0 * ay
                - _4q1
                + _8q1 * q1q1
                + _8q1 * q2q2
                + _4q1 * az
            )
            s2 = (
                4 * q0q0 * q2
                + _2q0 * ax
                + _4q2 * q3q3
                - _2q3 * ay
                - _4q2
                + _8q2 * q1q1
                + _8q2 * q2q2
                + _4q2 * az
            )
            s3 = 4 * q1q1 * q3 - _2q1 * ax + 4 * q2q2 * q3 - _2q2 * ay
            sn = math.sqrt(s0 * s0 + s1 * s1 + s2 * s2 + s3 * s3)
            if sn > 0.0:
                b = self.beta / sn
                qd0 -= b * s0
                qd1 -= b * s1
                qd2 -= b * s2
                qd3 -= b * s3
        self.q = quat_normalize((q0 + qd0 * dt, q1 + qd1 * dt, q2 + qd2 * dt, q3 + qd3 * dt))

    # -- convenience
    def to_world(self, v: Vec3) -> Vec3:
        return quat_rotate(self.q, v)

    def to_hat(self, v: Vec3) -> Vec3:
        return quat_rotate_inv(self.q, v)

    @property
    def tilt_deg(self) -> float:
        return tilt_deg_of(self.q)

    @property
    def roll_pitch_deg(self) -> tuple[float, float]:
        return roll_pitch_deg(self.q)
