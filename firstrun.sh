#!/bin/bash
# =============================================================================
#  firstrun.sh — Runs automatically on first Pi boot (as root)
#
#  Placed on the SD boot partition by prepare-sd.sh.
#  Triggered via cmdline.txt: systemd.run=/boot/firmware/firstrun.sh
#
#  What it does:
#    1. Waits for network
#    2. Copies monitoring files from boot partition to /home/dev/monitoring/
#    3. Runs install.sh
#    4. Sends Telegram "Pi is ready" notification
#    5. Removes itself from cmdline.txt so it never runs again
# =============================================================================

exec > /var/log/firstrun.log 2>&1
set -euo pipefail

# ── Detect boot partition path (Bookworm+ uses /boot/firmware, older uses /boot)
if [ -d /boot/firmware ]; then
    BOOT_DIR=/boot/firmware
else
    BOOT_DIR=/boot
fi

PI_USER="dev"
PI_HOME="/home/${PI_USER}"
MONITORING_DIR="${PI_HOME}/monitoring"

echo "[$(date)] ── firstrun.sh started ──"
echo "[$(date)] Boot dir: ${BOOT_DIR}"

# ── Wait for network (up to 60s) ─────────────────────────────────────────────
echo "[$(date)] Waiting for network..."
for i in $(seq 1 30); do
    if ping -c 1 -W 2 8.8.8.8 &>/dev/null; then
        echo "[$(date)] Network is up (attempt ${i})"
        break
    fi
    echo "[$(date)] No network yet, retrying in 2s... (${i}/30)"
    sleep 2
done

# ── Ensure user exists ────────────────────────────────────────────────────────
if ! id "${PI_USER}" &>/dev/null; then
    echo "[$(date)] Creating user ${PI_USER}..."
    useradd -m -s /bin/bash -G sudo "${PI_USER}"
    echo "${PI_USER}:monitoring" | chpasswd
    echo "[$(date)] User ${PI_USER} created with default password 'monitoring' — change it!"
else
    echo "[$(date)] User ${PI_USER} already exists."
fi

# ── Copy monitoring files from boot partition ─────────────────────────────────
echo "[$(date)] Copying monitoring files to ${MONITORING_DIR}..."
mkdir -p "${MONITORING_DIR}"
cp -r "${BOOT_DIR}/monitoring/." "${MONITORING_DIR}/"

# config.yaml sits separately on the boot partition for easy editing before insert
if [ -f "${BOOT_DIR}/config.yaml" ]; then
    cp "${BOOT_DIR}/config.yaml" "${MONITORING_DIR}/config.yaml"
    echo "[$(date)] config.yaml copied."
fi

chown -R "${PI_USER}:${PI_USER}" "${MONITORING_DIR}"
chmod +x "${MONITORING_DIR}/install.sh"

# ── Run installer ─────────────────────────────────────────────────────────────
echo "[$(date)] Running install.sh (this takes 5-15 minutes)..."
cd "${MONITORING_DIR}"
SUDO_USER="${PI_USER}" bash ./install.sh
echo "[$(date)] install.sh complete."

# ── Send Telegram notification ────────────────────────────────────────────────
echo "[$(date)] Sending Telegram notification..."
TOKEN=$(grep 'bot_token' "${MONITORING_DIR}/config.yaml" | awk -F'"' '{print $2}' | head -1)
CHAT=$(grep 'chat_id'  "${MONITORING_DIR}/config.yaml" | awk -F'"' '{print $2}' | head -1)
PI_IP=$(hostname -I | awk '{print $1}')
HOSTNAME=$(hostname)

if [ -n "${TOKEN}" ] && [ -n "${CHAT}" ] && [[ "${TOKEN}" != *"YOUR_"* ]]; then
    curl -s -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${CHAT}" \
        --data-urlencode "parse_mode=HTML" \
        --data-urlencode "text=✅ <b>${HOSTNAME} is ready!</b>
IP: ${PI_IP}
Grafana: http://${PI_IP}:3000
Pi-hole: http://${PI_IP}/admin" \
        &>/dev/null && echo "[$(date)] Telegram notification sent." \
                     || echo "[$(date)] Telegram notification failed (non-fatal)."
else
    echo "[$(date)] Telegram not configured — skipping notification."
fi

# ── Remove firstrun trigger from cmdline.txt ──────────────────────────────────
echo "[$(date)] Removing firstrun trigger from cmdline.txt..."
sed -i 's| systemd\.run=[^ ]*||g' "${BOOT_DIR}/cmdline.txt" || true
sed -i 's| systemd\.run_success_action=[^ ]*||g' "${BOOT_DIR}/cmdline.txt" || true
sed -i 's| systemd\.unit=[^ ]*||g' "${BOOT_DIR}/cmdline.txt" || true

echo "[$(date)] ── firstrun.sh done. Rebooting in 5s... ──"
sleep 5
reboot
