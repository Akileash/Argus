#!/usr/bin/env bash
# Argus device uninstall: stops and removes the systemd service.
#
# Leaves in place (on purpose):
#   /etc/argus/config.yaml   (team config and device token)
#   device/var/, device/recordings/   (clips queue and recordings)
#   device/.venv              (Python environment)
#   apt packages, boot config, user groups, journald cap
set -euo pipefail

readonly SERVICE_NAME="argus.service"
readonly UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}"

log()  { printf '==> %s\n' "$*"; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

if [[ "$(id -u)" -eq 0 ]]; then
  die "run as your normal user. The script uses sudo itself."
fi

sudo -v || die "sudo is required"

if sudo systemctl cat "${SERVICE_NAME}" >/dev/null 2>&1; then
  log "Stopping and disabling ${SERVICE_NAME}"
  sudo systemctl stop "${SERVICE_NAME}" || true
  sudo systemctl disable "${SERVICE_NAME}" || true
else
  log "${SERVICE_NAME} is not installed in systemd. Nothing to stop."
fi

if sudo test -f "${UNIT_PATH}"; then
  log "Removing ${UNIT_PATH}"
  sudo rm -f "${UNIT_PATH}"
fi

sudo systemctl daemon-reload
sudo systemctl reset-failed "${SERVICE_NAME}" >/dev/null 2>&1 || true

log "Service removed. Kept: /etc/argus/config.yaml, device/var/, device/recordings/, device/.venv"
