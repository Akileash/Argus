#!/usr/bin/env bash
# Argus smart hard hat: device install script.
#
# Target: Raspberry Pi 4, Raspberry Pi OS Bookworm 64-bit Lite, run as a normal
# user (NOT root). The script calls sudo for the steps that need it.
#
# Usage:  device/install.sh [--force]
#   --force  skip the "is this a Raspberry Pi?" checks (for testing on other Linux boxes)
#
# Idempotent: safe to re-run after a failure or after pulling new code.
set -euo pipefail

readonly SERVICE_NAME="argus.service"
readonly UNIT_TEMPLATE_NAME="argus.service"
readonly SYSTEMD_UNIT_DIR="/etc/systemd/system"
readonly ARGUS_CONF_DIR="/etc/argus"
readonly ARGUS_CONFIG="${ARGUS_CONF_DIR}/config.yaml"
readonly JOURNALD_DROPIN_DIR="/etc/systemd/journald.conf.d"
readonly JOURNALD_DROPIN="${JOURNALD_DROPIN_DIR}/argus.conf"
readonly BOOT_CONFIG="/boot/firmware/config.txt"
# The haptics driver uses lgpio software PWM (tx_pwm) on GPIO12/13, so the hardware-PWM overlay is NOT
# installed (it would put those pins in the PWM alt function and could block the GPIO claim).
readonly OLD_PWM_OVERLAY="dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4"
# The IMU is read at 1 kHz over I2C; 400 kHz is needed for the FIFO bursts to keep up.
readonly I2C_BAUD_LINE="dtparam=i2c_arm_baudrate=400000"
readonly PYTHON_BIN="/usr/bin/python3"

FORCE=0
REBOOT_NEEDED=0

DEVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly DEVICE_DIR
RUN_USER="$(id -un)"
readonly RUN_USER

log()  { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: device/install.sh [--force]

Installs the Argus device runtime on a Raspberry Pi (Bookworm 64-bit Lite).
Run it as your normal user, not root. It asks for sudo when needed.

  --force   skip the Raspberry Pi hardware checks
EOF
}

parse_args() {
  local arg
  for arg in "$@"; do
    case "${arg}" in
      --force) FORCE=1 ;;
      -h|--help) usage; exit 0 ;;
      *) die "unknown argument: ${arg} (see --help)" ;;
    esac
  done
}

# Refuse to run anywhere except a Raspberry Pi, unless --force is given.
check_platform() {
  log "Checking platform"
  if [[ "$(id -u)" -eq 0 ]]; then
    die "do not run as root. uv and the venv must belong to your user. The script uses sudo itself."
  fi

  local model="" arch
  arch="$(uname -m)"
  if [[ -r /proc/device-tree/model ]]; then
    model="$(tr -d '\0' < /proc/device-tree/model)"
  fi

  if [[ "$(uname -s)" != "Linux" || "${arch}" != "aarch64" ]] || [[ "${model}" != *"Raspberry Pi"* ]]; then
    if [[ "${FORCE}" -eq 1 ]]; then
      warn "not a Raspberry Pi (arch=${arch}, model='${model:-unknown}'). Continuing because of --force."
    else
      die "this must run on a Raspberry Pi (Linux aarch64, model contains 'Raspberry Pi'). Found: arch=${arch}, model='${model:-unknown}'. Pass --force to override."
    fi
  else
    info "Raspberry Pi detected: ${model}"
  fi
}

# Ask for the sudo password once, up front, so later steps don't stall.
ensure_sudo() {
  log "Checking sudo access"
  sudo -v || die "sudo is required (the user must be in the sudo group)"
}

install_apt_packages() {
  log "Installing apt packages"
  # picamera2 and libcamera come from apt, not pip: the libcamera Python bindings
  # are built against the system libcamera and are not on PyPI.
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    ffmpeg python3-picamera2 python3-libcamera i2c-tools git curl
}

# Append a line to a file only if it is not already present (exact match).
ensure_boot_line() {
  local line="$1"
  if sudo grep -qxF "${line}" "${BOOT_CONFIG}"; then
    info "already present: ${line}"
  else
    printf '%s\n' "${line}" | sudo tee -a "${BOOT_CONFIG}" >/dev/null
    info "added: ${line}"
    REBOOT_NEEDED=1
  fi
}

configure_boot() {
  log "Enabling I2C (400 kHz) and the camera"
  [[ -f "${BOOT_CONFIG}" ]] || die "${BOOT_CONFIG} not found. Is this Bookworm?"

  sudo raspi-config nonint do_i2c 0
  info "I2C enabled via raspi-config"

  # Bookworm detects the camera automatically. Add the flag only if it is missing.
  if sudo grep -qE '^camera_auto_detect=0' "${BOOT_CONFIG}"; then
    warn "camera_auto_detect=0 is set in ${BOOT_CONFIG}. Change it to 1 for the hat camera."
  else
    ensure_boot_line "camera_auto_detect=1"
  fi

  ensure_boot_line "${I2C_BAUD_LINE}"

  # Earlier versions of this script added a hardware-PWM overlay. The motors use software PWM, so say so
  # instead of silently editing a boot file the user may have changed.
  if sudo grep -qxF "${OLD_PWM_OVERLAY}" "${BOOT_CONFIG}"; then
    warn "${BOOT_CONFIG} still has '${OLD_PWM_OVERLAY}' from an older install. The haptics driver uses"
    warn "software PWM, so remove that line: sudo sed -i.bak '/^dtoverlay=pwm-2chan/d' ${BOOT_CONFIG}   then reboot."
  fi

  if [[ ! -e /dev/i2c-1 ]]; then
    REBOOT_NEEDED=1
  fi
}

add_user_groups() {
  log "Adding ${RUN_USER} to hardware groups"
  local groups=() grp
  for grp in i2c gpio video; do
    if getent group "${grp}" >/dev/null; then
      groups+=("${grp}")
    else
      warn "group '${grp}' does not exist on this system. Skipping it."
    fi
  done
  if [[ "${#groups[@]}" -gt 0 ]]; then
    sudo usermod -aG "$(IFS=,; echo "${groups[*]}")" "${RUN_USER}"
    info "groups: ${groups[*]} (takes effect at next login or service start)"
  fi
}

install_uv() {
  log "Checking uv"
  export PATH="${HOME}/.local/bin:${PATH}"
  if command -v uv >/dev/null 2>&1; then
    info "uv already installed: $(uv --version)"
    return
  fi
  # Official installer. It is downloaded to a temp file first, so a failed
  # download does not get piped straight into sh.
  local tmp
  tmp="$(mktemp)"
  curl -LsSf https://astral.sh/uv/install.sh -o "${tmp}"
  sh "${tmp}"
  rm -f "${tmp}"
  command -v uv >/dev/null 2>&1 || die "uv installed but not on PATH. Add ~/.local/bin to PATH and re-run."
  info "uv installed: $(uv --version)"
}

sync_python_env() {
  log "Creating Python environment (Python 3.11, system site-packages, extra: pi)"
  [[ -x "${PYTHON_BIN}" ]] || die "${PYTHON_BIN} not found"
  "${PYTHON_BIN}" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 11) else 1)' \
    || die "${PYTHON_BIN} is not Python 3.11. Bookworm should ship 3.11."

  local venv="${DEVICE_DIR}/.venv"
  # --system-site-packages makes the apt-installed picamera2 and libcamera visible.
  # Recreate the venv if an earlier run made it without that flag.
  if [[ -f "${venv}/pyvenv.cfg" ]] \
     && ! grep -qE '^include-system-site-packages = true' "${venv}/pyvenv.cfg"; then
    info "existing venv lacks system site-packages. Recreating it."
    rm -rf "${venv}"
  fi
  if [[ ! -d "${venv}" ]]; then
    (cd "${DEVICE_DIR}" && uv venv --python "${PYTHON_BIN}" --system-site-packages --no-python-downloads "${venv}")
  fi

  (cd "${DEVICE_DIR}" && uv sync --locked --extra pi)
  info "venv ready: ${venv}"
}

prepare_dirs_and_config() {
  log "Preparing directories and config"
  mkdir -p "${DEVICE_DIR}/var"
  info "clips queue dir: ${DEVICE_DIR}/var"

  sudo install -d -m 0755 "${ARGUS_CONF_DIR}"
  if sudo test -f "${ARGUS_CONFIG}"; then
    info "keeping existing ${ARGUS_CONFIG} (never overwritten)"
  else
    [[ -f "${DEVICE_DIR}/config.yaml" ]] || die "no config template at ${DEVICE_DIR}/config.yaml"
    # Owned by the service user so 'argus-cli pair' can write the device token without sudo.
    sudo install -m 0640 -o "${RUN_USER}" -g "$(id -gn)" "${DEVICE_DIR}/config.yaml" "${ARGUS_CONFIG}"
    info "installed ${ARGUS_CONFIG}"
  fi
}

# Escape a value for use in a sed s||| replacement.
sed_escape() {
  printf '%s' "$1" | sed -e 's/[\/&|]/\\&/g'
}

install_unit() {
  log "Installing systemd unit"
  local template="${DEVICE_DIR}/${UNIT_TEMPLATE_NAME}"
  [[ -f "${template}" ]] || die "unit template not found: ${template}"

  local tmp
  tmp="$(mktemp)"
  sed \
    -e "s|__DEVICE_DIR__|$(sed_escape "${DEVICE_DIR}")|g" \
    -e "s|__ARGUS_USER__|$(sed_escape "${RUN_USER}")|g" \
    "${template}" > "${tmp}"

  if sudo cmp -s "${tmp}" "${SYSTEMD_UNIT_DIR}/${SERVICE_NAME}" 2>/dev/null; then
    info "unit unchanged"
  else
    sudo install -m 0644 "${tmp}" "${SYSTEMD_UNIT_DIR}/${SERVICE_NAME}"
    info "installed ${SYSTEMD_UNIT_DIR}/${SERVICE_NAME}"
  fi
  rm -f "${tmp}"

  sudo systemctl daemon-reload
  sudo systemctl enable "${SERVICE_NAME}"
  info "enabled ${SERVICE_NAME} (starts at boot)"
}

configure_journald() {
  log "Capping journald log size"
  local tmp
  tmp="$(mktemp)"
  printf '[Journal]\nSystemMaxUse=200M\n' > "${tmp}"
  sudo install -d -m 0755 "${JOURNALD_DROPIN_DIR}"
  if sudo cmp -s "${tmp}" "${JOURNALD_DROPIN}" 2>/dev/null; then
    info "journald cap unchanged"
  else
    sudo install -m 0644 "${tmp}" "${JOURNALD_DROPIN}"
    sudo systemctl restart systemd-journald
    info "installed ${JOURNALD_DROPIN} (SystemMaxUse=200M)"
  fi
  rm -f "${tmp}"
}

# Prints "yes" if /etc/argus/config.yaml has a non-empty backend.device_token.
device_is_paired() {
  local venv_py="${DEVICE_DIR}/.venv/bin/python"
  [[ -x "${venv_py}" ]] || { echo "no"; return; }
  "${venv_py}" -c '
import sys, yaml
with open(sys.argv[1]) as fh:
    cfg = yaml.safe_load(fh) or {}
token = (cfg.get("backend") or {}).get("device_token") or ""
print("yes" if str(token).strip() else "no")
' "${ARGUS_CONFIG}"
}

start_or_explain() {
  log "Starting service"
  if [[ "${REBOOT_NEEDED}" -eq 1 ]]; then
    info "boot config changed. The service is enabled and will start after the reboot."
    return
  fi
  if [[ "$(device_is_paired)" == "yes" ]]; then
    sudo systemctl restart "${SERVICE_NAME}"
    info "service started (restarted to load the current code)"
  else
    info "device is not paired yet, so the service was NOT started now."
    info "It IS enabled: after any reboot it starts by itself and queues events locally until paired."
    info "Pair the device (works while the service runs; the uploader picks the token up within ~10 s):"
    info "  cd ${DEVICE_DIR} && .venv/bin/argus-cli pair"
    info "Start it now:  sudo systemctl start ${SERVICE_NAME}"
  fi
}

print_checklist() {
  cat <<EOF

================================================================
Argus device install finished.
================================================================
EOF
  if [[ "${REBOOT_NEEDED}" -eq 1 ]]; then
    cat <<EOF
REBOOT REQUIRED. I2C (400 kHz) and camera boot settings changed.
  1. sudo reboot
  2. after boot, re-run this script (it is idempotent), or check the items below.
EOF
  fi
  cat <<EOF

Checklist:
  [ ] Hardware bus scan:    i2cdetect -y 1
        expect 0x29 (VL53L0X), 0x62 (SCD41), 0x68 (MPU-6050)
  [ ] Camera detected:      rpicam-hello --list-cameras   (libcamera-hello on older images)
  [ ] IMU test:             ${DEVICE_DIR}/.venv/bin/argus-cli test imu
  [ ] Haptics test:         ${DEVICE_DIR}/.venv/bin/argus-cli buzz both
  [ ] Device paired:        ${DEVICE_DIR}/.venv/bin/argus-cli pair   (if not done)
  [ ] Service running:      systemctl status ${SERVICE_NAME}
  [ ] Live logs:            journalctl -u ${SERVICE_NAME} -f

Config:  ${ARGUS_CONFIG}
Stop/remove the service:  ${DEVICE_DIR}/uninstall.sh
EOF
}

main() {
  parse_args "$@"
  check_platform
  ensure_sudo
  install_apt_packages
  configure_boot
  add_user_groups
  install_uv
  sync_python_env
  prepare_dirs_and_config
  install_unit
  configure_journald
  start_or_explain
  print_checklist
}

main "$@"
