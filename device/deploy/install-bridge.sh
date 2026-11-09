#!/usr/bin/env bash
# Argus cloud bridge: install script for the Pi that runs the teammates' hard-hat code.
#
# Usage:  ~/argus-cloud/device/deploy/install-bridge.sh [--allow-path]
#   --allow-path  skip the check that this checkout lives in ~/argus-cloud
#
# What it does (idempotent, run as your normal user, not root; it calls sudo for the steps that need it):
#   1. refuses to install into ~/argus (that is the teammates' tree, which this never touches)
#   2. installs uv (no uv on the Pi by default) and ffmpeg (clip remux) if missing
#   3. creates device/.venv in THIS checkout with the Python our pyproject needs (3.11, downloaded by uv if the
#      Pi has another one, e.g. Debian 13 ships 3.13). No hardware extras: the bridge never opens I2C/GPIO/camera
#   4. creates /etc/argus/config.yaml from device/config.yaml if missing (never overwritten), owned by you
#   5. installs and enables argus-bridge.service (starts it only if the device is already paired)
# It does NOT edit ~/argus, /boot or any system hardware config, and does not enable their argus-sensors.service.
#
# Layout:  ~/argus         their tree (hat/, pi_sensors/, events.jsonl, clips/)   -- theirs, read only for us
#          ~/argus-cloud   a git checkout of branch integration/cloud-bridge      -- this script lives here
#
# Then pair once:  cd ~/argus-cloud/device && .venv/bin/argus-cli pair --config /etc/argus/config.yaml
set -euo pipefail

readonly SERVICE_NAME="argus-bridge.service"
readonly SYSTEMD_UNIT_DIR="/etc/systemd/system"
readonly ARGUS_CONF_DIR="/etc/argus"
readonly ARGUS_CONFIG="${ARGUS_CONF_DIR}/config.yaml"
readonly PY_VERSION="3.11"
readonly CLOUD_DIR="${HOME}/argus-cloud"
readonly THEIR_DIR="${HOME}/argus"

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly DEPLOY_DIR
DEVICE_DIR="$(cd "${DEPLOY_DIR}/.." && pwd)"
readonly DEVICE_DIR
RUN_USER="$(id -un)"
readonly RUN_USER
ALLOW_PATH=0

log()  { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

for arg in "$@"; do
  case "${arg}" in
    --allow-path) ALLOW_PATH=1 ;;
    -h | --help) sed -n '2,22p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown argument: ${arg} (see --help)" ;;
  esac
done

check_location() {
  log "Checking where this is installed"
  [[ "$(id -u)" -ne 0 ]] || die "do not run as root; the script uses sudo itself."
  case "${DEVICE_DIR}/" in
    "${THEIR_DIR}/"*) die "${DEVICE_DIR} is inside ${THEIR_DIR}, the teammates' tree. Clone this branch to ${CLOUD_DIR} instead." ;;
  esac
  if [[ "${DEVICE_DIR}" != "${CLOUD_DIR}/device" && "${ALLOW_PATH}" -ne 1 ]]; then
    die "expected ${CLOUD_DIR}/device, found ${DEVICE_DIR}. Clone to ${CLOUD_DIR} (or pass --allow-path)."
  fi
  info "bridge checkout: ${DEVICE_DIR}"
  [[ -x "${THEIR_DIR}/.venv/bin/python" ]] || warn "${THEIR_DIR}/.venv/bin/python not found: set bridge.python in ${ARGUS_CONFIG}"
  [[ -f "${THEIR_DIR}/hat/main.py" ]] || warn "${THEIR_DIR}/hat/main.py not found: set bridge.argus_dir in ${ARGUS_CONFIG}"
}

install_tools() {
  log "Checking uv and ffmpeg"
  export PATH="${HOME}/.local/bin:${PATH}"
  if command -v uv >/dev/null 2>&1; then
    info "uv already installed: $(uv --version)"
  else
    local tmp
    tmp="$(mktemp)"
    curl -LsSf https://astral.sh/uv/install.sh -o "${tmp}"
    sh "${tmp}"
    rm -f "${tmp}"
    command -v uv >/dev/null 2>&1 || die "uv installed but not on PATH. Add ~/.local/bin to PATH and re-run."
    info "uv installed: $(uv --version)"
  fi
  if command -v ffmpeg >/dev/null 2>&1; then
    info "ffmpeg present"
  else
    sudo -v || die "sudo is required to install ffmpeg"
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg
  fi
}

sync_python_env() {
  log "Creating the Python ${PY_VERSION} environment (no hardware libraries)"
  # --python 3.11: uv uses a system 3.11 if there is one, otherwise downloads a managed build.
  (cd "${DEVICE_DIR}" && uv sync --locked --no-dev --python "${PY_VERSION}")
  info "venv ready: ${DEVICE_DIR}/.venv"
}

prepare_config() {
  log "Preparing ${ARGUS_CONF_DIR}"
  sudo -v || die "sudo is required"
  sudo install -d -m 0755 "${ARGUS_CONF_DIR}"
  if sudo test -f "${ARGUS_CONFIG}"; then
    info "keeping existing ${ARGUS_CONFIG} (never overwritten)"
  else
    sudo install -m 0640 -o "${RUN_USER}" -g "$(id -gn)" "${DEVICE_DIR}/config.yaml" "${ARGUS_CONFIG}"
    info "installed ${ARGUS_CONFIG} (edit its bridge: section if your paths differ)"
  fi
  mkdir -p "${DEVICE_DIR}/var"
}

sed_escape() { printf '%s' "$1" | sed -e 's/[\/&|]/\\&/g'; }

install_unit() {
  log "Installing systemd unit"
  local template="${DEPLOY_DIR}/${SERVICE_NAME}" tmp
  [[ -f "${template}" ]] || die "unit template not found: ${template}"
  tmp="$(mktemp)"
  sed -e "s|__DEVICE_DIR__|$(sed_escape "${DEVICE_DIR}")|g" \
      -e "s|__ARGUS_USER__|$(sed_escape "${RUN_USER}")|g" "${template}" > "${tmp}"
  if sudo cmp -s "${tmp}" "${SYSTEMD_UNIT_DIR}/${SERVICE_NAME}" 2>/dev/null; then
    info "unit unchanged"
  else
    sudo install -m 0644 "${tmp}" "${SYSTEMD_UNIT_DIR}/${SERVICE_NAME}"
    info "installed ${SYSTEMD_UNIT_DIR}/${SERVICE_NAME}"
  fi
  rm -f "${tmp}"
  sudo systemctl daemon-reload
  sudo systemctl enable "${SERVICE_NAME}"
}

warn_conflicts() {
  local unit
  for unit in argus.service argus-sensors.service; do
    if systemctl is-enabled "${unit}" >/dev/null 2>&1 || systemctl is-active "${unit}" >/dev/null 2>&1; then
      warn "${unit} is enabled or active. Do not run it together with ${SERVICE_NAME}:"
      warn "  argus.service is our mock-sensor runtime (second uploader); argus-sensors.service passes --tof-bus 3 and"
      warn "  fights hat.main for the VL6180X. Stop it with: sudo systemctl disable --now ${unit}"
    fi
  done
  if pgrep -f "python.* -m hat.main" >/dev/null 2>&1 && ! systemctl is-active "${SERVICE_NAME}" >/dev/null 2>&1; then
    warn "a hat.main is already running. Stop it before starting ${SERVICE_NAME} (the bridge launches its own)."
  fi
}

is_paired() {
  "${DEVICE_DIR}/.venv/bin/python" -c '
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1])) or {}
print("yes" if str((cfg.get("backend") or {}).get("device_token") or "").strip() else "no")
' "${ARGUS_CONFIG}"
}

finish() {
  log "Done"
  if [[ "$(is_paired)" == "yes" ]]; then
    sudo systemctl restart "${SERVICE_NAME}"
    info "paired: ${SERVICE_NAME} (re)started"
  else
    info "not paired yet, so ${SERVICE_NAME} was NOT started. Pair, then start it:"
    info "  cd ${DEVICE_DIR} && .venv/bin/argus-cli pair --config ${ARGUS_CONFIG}"
    info "  sudo systemctl start ${SERVICE_NAME}"
  fi
  info "logs:  journalctl -u ${SERVICE_NAME} -f      (their hat.main output is passed through unchanged)"
  info "grep the journal for 'INTEGRATION ISSUE' to see mapping / contract / timing problems."
}

check_location
install_tools
sync_python_env
prepare_config
install_unit
warn_conflicts
finish
