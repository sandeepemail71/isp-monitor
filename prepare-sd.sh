#!/usr/bin/env bash
# =============================================================================
#  prepare-sd.sh — Prepare a freshly flashed Pi SD card for auto-setup
#
#  Run this on your Mac AFTER flashing Pi OS with Raspberry Pi Imager,
#  BEFORE ejecting the SD card.
#
#  Usage:
#    ./prepare-sd.sh                        # auto-detect boot partition
#    ./prepare-sd.sh /Volumes/bootfs        # specify mount path
#
#  Prerequisites:
#    1. Flash Raspberry Pi OS Lite (64-bit) using Raspberry Pi Imager
#    2. In Imager advanced options (gear icon):
#         - Set hostname: wan-monitoring
#         - Enable SSH (password auth)
#         - Set username: dev, password: <your choice>
#    3. SD card should be mounted (Imager usually keeps it mounted after flash)
#
#  What it does:
#    1. Copies all monitoring files onto the SD boot partition
#    2. Copies config.yaml separately (easy to edit before inserting SD)
#    3. Copies firstrun.sh onto the boot partition
#    4. Modifies cmdline.txt to trigger firstrun.sh on first boot
#
#  After running:
#    - Eject SD card
#    - Insert into Pi, power on
#    - Wait 5-15 minutes
#    - Receive Telegram notification when ready
# =============================================================================

set -euo pipefail

BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Find boot partition ───────────────────────────────────────────────────────
if [ -n "${1:-}" ]; then
    BOOT="${1}"
else
    # Auto-detect: look for a volume with cmdline.txt (Pi boot partition signature)
    BOOT=""
    for vol in /Volumes/*/; do
        if [ -f "${vol}cmdline.txt" ]; then
            BOOT="${vol%/}"
            break
        fi
    done
fi

if [ -z "${BOOT}" ]; then
    err "Could not find Pi boot partition. Specify it manually: ./prepare-sd.sh /Volumes/bootfs"
fi

if [ ! -f "${BOOT}/cmdline.txt" ]; then
    err "${BOOT} does not look like a Pi boot partition (no cmdline.txt found)."
fi

echo ""
echo -e "${BOLD}Preparing SD card at: ${BOOT}${NC}"
echo ""

# ── Pick config file (prefer config.local.yaml if it exists) ─────────────────
if [ -f "${SCRIPT_DIR}/config.local.yaml" ]; then
    CONFIG_FILE="${SCRIPT_DIR}/config.local.yaml"
    info "Using config.local.yaml"
else
    CONFIG_FILE="${SCRIPT_DIR}/config.yaml"
    warn "config.local.yaml not found — using config.yaml"
fi

if grep -q "YOUR_BOT_TOKEN\|YOUR_CHAT_ID\|YOUR_APP_PASSWORD" "${CONFIG_FILE}"; then
    warn "${CONFIG_FILE} still has placeholder values!"
    warn "Edit it with your real Telegram/email credentials before continuing."
    echo ""
    read -rp "Continue anyway? (y/N): " confirm
    [[ "${confirm}" =~ ^[Yy]$ ]] || exit 1
fi

# ── Copy monitoring files ─────────────────────────────────────────────────────
info "Copying monitoring files to ${BOOT}/monitoring/..."
rm -rf "${BOOT}/monitoring"
rsync -a \
    --exclude '.git' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude 'logs/' \
    --exclude 'data/' \
    --exclude 'config.yaml' \
    --exclude 'firstrun.sh' \
    --exclude 'prepare-sd.sh' \
    --exclude 'deploy.sh' \
    "${SCRIPT_DIR}/" "${BOOT}/monitoring/"

# ── Copy config (prefer config.local.yaml) ───────────────────────────────────
info "Copying config to ${BOOT}/config.yaml..."
cp "${CONFIG_FILE}" "${BOOT}/config.yaml"

# ── Copy firstrun.sh ──────────────────────────────────────────────────────────
info "Copying firstrun.sh to ${BOOT}/firstrun.sh..."
cp "${SCRIPT_DIR}/firstrun.sh" "${BOOT}/firstrun.sh"
# Ensure Unix line endings (important for bash on Pi)
sed -i '' 's/\r//' "${BOOT}/firstrun.sh" 2>/dev/null || true

# ── Modify cmdline.txt ────────────────────────────────────────────────────────
info "Updating cmdline.txt to trigger firstrun.sh on first boot..."
CMDLINE="${BOOT}/cmdline.txt"
CMDLINE_ORIG=$(cat "${CMDLINE}")

# Remove any existing firstrun trigger (idempotent)
CMDLINE_CLEAN=$(echo "${CMDLINE_ORIG}" \
    | sed 's| systemd\.run=[^ ]*||g' \
    | sed 's| systemd\.run_success_action=[^ ]*||g' \
    | sed 's| systemd\.unit=[^ ]*||g' \
    | tr -d '\n')

# Append firstrun trigger (must be on a single line)
echo "${CMDLINE_CLEAN} systemd.run=/boot/firmware/firstrun.sh systemd.run_success_action=none systemd.unit=kernel-command-line.target" > "${CMDLINE}"

info "cmdline.txt updated."

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}✓ SD card is ready!${NC}"
echo ""
echo "  Files written to ${BOOT}:"
echo "    monitoring/     — all project files"
echo "    config.yaml     — edit this on the SD if needed before inserting"
echo "    firstrun.sh     — auto-runs on first boot"
echo ""
echo -e "${YELLOW}  OPTIONAL: Edit credentials directly on the SD before inserting:${NC}"
echo "    open ${BOOT}/config.yaml"
echo ""
echo "  Next steps:"
echo "    1. Eject the SD card"
echo "    2. Insert into Pi and power on"
echo "    3. Wait 5-15 minutes for setup to complete"
echo "    4. Receive Telegram notification when Pi is ready"
echo ""
echo "  Setup log (after boot): ssh dev@<PI_IP> 'cat /var/log/firstrun.log'"
echo ""
