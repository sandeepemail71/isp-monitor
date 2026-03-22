#!/usr/bin/env bash
# =============================================================================
#  deploy.sh — Push this monitoring stack to any Raspberry Pi
#
#  Usage:
#    ./deploy.sh <PI_IP>              # default user: dev
#    ./deploy.sh <PI_IP> <PI_USER>    # custom user
#
#  Example:
#    ./deploy.sh 192.168.68.116
#    ./deploy.sh 192.168.68.120 pi
#
#  What it does:
#    1. Copies all project files to the Pi
#    2. Runs install.sh on the Pi (installs InfluxDB, Grafana, Pi-hole, etc.)
# =============================================================================

set -euo pipefail

PI_IP="${1:?Usage: ./deploy.sh <PI_IP> [PI_USER]}"
PI_USER="${2:-dev}"
REMOTE="${PI_USER}@${PI_IP}"
REMOTE_DIR="/home/${PI_USER}/monitoring"

echo ""
echo "Deploying to ${REMOTE}:${REMOTE_DIR}"
echo ""

# ── Step 1: Copy all files ───────────────────────────────────────────────────
echo "→ Copying files..."
rsync -az --progress \
    --exclude '.git' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude 'logs/' \
    --exclude 'data/' \
    --exclude 'config.local.yaml' \
    . "${REMOTE}:${REMOTE_DIR}/"

# Copy config.local.yaml as config.yaml on the Pi if it exists
if [ -f "config.local.yaml" ]; then
    echo "→ Copying config.local.yaml as config.yaml on Pi..."
    rsync -az config.local.yaml "${REMOTE}:${REMOTE_DIR}/config.yaml"
fi

# ── Step 2: Run installer ────────────────────────────────────────────────────
echo ""
echo "→ Running installer on Pi..."
ssh "${REMOTE}" "cd ${REMOTE_DIR} && sudo ./install.sh"
