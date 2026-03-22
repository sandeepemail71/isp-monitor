#!/usr/bin/env bash
# =============================================================================
#  Home ISP Monitor — One-Shot Installer
#  Raspberry Pi 3B | Raspberry Pi OS | No Docker
#
#  Usage:  sudo ./install.sh
#
#  Steps:
#    1. System packages
#    2. InfluxDB 1.8 (direct .deb — avoids GPG issues on Trixie)
#    3. Grafana OSS
#    4. Pi-hole (unattended DNS + ad blocking)
#    5. Python virtualenv + dependencies
#    6. Grafana provisioning (datasource + ISP dashboard)
#    7. Systemd services (ping-monitor, http-check)
#    8. Cron jobs (speedtest, health-check, weekly-report)
#    9. Convenience CLI commands
#   10. InfluxDB 90-day retention policy
#   11. Final status check
# =============================================================================
set -euo pipefail

# ── Colour helpers ──────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }
step()  { echo -e "\n${BOLD}${BLUE}━━ $* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"; }

# ── Guard: must run as root ─────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    err "Run with sudo: sudo ./install.sh"
    exit 1
fi

# ── Resolve paths ───────────────────────────────────────────────────────────
PI_USER="${SUDO_USER:-dev}"
PI_HOME="/home/${PI_USER}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MONITORING_DIR="${PI_HOME}/monitoring"
INFLUX_DB="home_monitoring"
PIHOLE_INTERFACE="eth0"

info "Installing as user: ${PI_USER}"
info "Monitoring directory: ${MONITORING_DIR}"

. /etc/os-release
info "OS: ${PRETTY_NAME} (${VERSION_CODENAME:-unknown})"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — System packages
# ─────────────────────────────────────────────────────────────────────────────
step "Step 1/11 — System packages"
apt-get update -q
apt-get upgrade -y -q
apt-get install -y -q \
    python3 python3-pip python3-venv \
    curl wget git \
    net-tools dnsutils gnupg apt-transport-https \
    ca-certificates lsb-release

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — InfluxDB 1.8
# Direct .deb download — avoids apt repo GPG failures on Debian Trixie (sqv)
# Service name is 'influxdb' (not 'influxd')
# ─────────────────────────────────────────────────────────────────────────────
step "Step 2/11 — InfluxDB 1.8"
INFLUX_DEB="influxdb_1.8.10_armhf.deb"
INFLUX_URL="https://dl.influxdata.com/influxdb/releases/${INFLUX_DEB}"

if ! systemctl is-active --quiet influxdb 2>/dev/null; then
    info "Downloading InfluxDB 1.8.10 (armhf)..."
    wget -q -O "/tmp/${INFLUX_DEB}" "${INFLUX_URL}"
    dpkg -i "/tmp/${INFLUX_DEB}"
    rm -f "/tmp/${INFLUX_DEB}"
    systemctl enable influxdb
    systemctl start influxdb
    info "Waiting for InfluxDB to start..."
    sleep 5
else
    info "InfluxDB already running — skipping install."
fi

info "Creating database '${INFLUX_DB}'..."
influx -execute "CREATE DATABASE ${INFLUX_DB}" 2>/dev/null || \
    warn "Could not create DB — may already exist or InfluxDB still starting."

# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — Grafana
# ─────────────────────────────────────────────────────────────────────────────
step "Step 3/11 — Grafana"
if ! systemctl is-active --quiet grafana-server 2>/dev/null; then
    curl -fsSL https://packages.grafana.com/gpg.key \
        | gpg --dearmor -o /usr/share/keyrings/grafana.gpg
    echo "deb [signed-by=/usr/share/keyrings/grafana.gpg] \
https://packages.grafana.com/oss/deb stable main" \
        > /etc/apt/sources.list.d/grafana.list
    apt-get update -q
    apt-get install -y -q grafana
    systemctl enable grafana-server
    systemctl start grafana-server
    info "Waiting for Grafana to start..."
    sleep 5
else
    info "Grafana already running — skipping install."
fi

# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — Pi-hole (unattended)
# ─────────────────────────────────────────────────────────────────────────────
step "Step 4/11 — Pi-hole"
if ! command -v pihole &>/dev/null; then

    # Free port 53 if anything is listening on it
    if ss -tlnp | grep -q ':53 '; then
        warn "Port 53 in use — disabling systemd-resolved DNS stub..."
        mkdir -p /etc/systemd/resolved.conf.d
        cat > /etc/systemd/resolved.conf.d/no-stub.conf << STUB_EOF
[Resolve]
DNSStubListener=no
STUB_EOF
        systemctl restart systemd-resolved || true
        sleep 2
    fi

    # Determine IP/CIDR for Pi-hole
    PI_CIDR=$(ip -4 -o addr show "${PIHOLE_INTERFACE}" 2>/dev/null \
        | awk '{print $4}' | head -1)
    if [[ -z "${PI_CIDR}" ]]; then
        warn "Could not detect IP on ${PIHOLE_INTERFACE}. Trying eth0..."
        PI_CIDR=$(ip -4 -o addr show eth0 2>/dev/null | awk '{print $4}' | head -1)
    fi
    info "Pi-hole will bind to: ${PI_CIDR} on ${PIHOLE_INTERFACE}"

    mkdir -p /etc/pihole
    cat > /etc/pihole/setupVars.conf << PIHOLE_EOF
PIHOLE_INTERFACE=${PIHOLE_INTERFACE}
IPV4_ADDRESS=${PI_CIDR}
QUERY_LOGGING=true
INSTALL_WEB_SERVER=true
INSTALL_WEB_INTERFACE=true
LIGHTTPD_ENABLED=true
PIHOLE_DNS_1=1.1.1.1
PIHOLE_DNS_2=8.8.8.8
DNS_FQDN_REQUIRED=false
DNS_BOGUS_PRIV=true
DNSMASQ_LISTENING=single
WEBPASSWORD=
BLOCKING_ENABLED=true
PIHOLE_EOF

    curl -sSL https://install.pi-hole.net \
        | PIHOLE_SKIP_OS_CHECK=true bash /dev/stdin --unattended
    info "Pi-hole installed."
else
    info "Pi-hole already installed — skipping."
fi

# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 — Copy monitoring files & Python virtualenv
# ─────────────────────────────────────────────────────────────────────────────
step "Step 5/11 — Monitoring files & Python venv"

if [[ "${SCRIPT_DIR}" != "${MONITORING_DIR}" ]]; then
    info "Copying files to ${MONITORING_DIR}..."
    mkdir -p "${MONITORING_DIR}"
    rsync -a --exclude '.git' --exclude '*.pyc' --exclude '__pycache__' \
        "${SCRIPT_DIR}/" "${MONITORING_DIR}/"
    chown -R "${PI_USER}:${PI_USER}" "${MONITORING_DIR}"
else
    info "Already in ${MONITORING_DIR} — no copy needed."
fi

sudo -u "${PI_USER}" mkdir -p "${MONITORING_DIR}/logs"
sudo -u "${PI_USER}" mkdir -p "${MONITORING_DIR}/data"

if [[ ! -f "${MONITORING_DIR}/venv/bin/activate" ]]; then
    info "Creating Python virtual environment..."
    sudo -u "${PI_USER}" python3 -m venv "${MONITORING_DIR}/venv"
fi

info "Installing Python dependencies..."
sudo -u "${PI_USER}" "${MONITORING_DIR}/venv/bin/pip" install --upgrade pip -q
sudo -u "${PI_USER}" "${MONITORING_DIR}/venv/bin/pip" install \
    -r "${MONITORING_DIR}/requirements.txt" -q

# ─────────────────────────────────────────────────────────────────────────────
# STEP 6 — Grafana provisioning (datasource + ISP dashboard)
# ─────────────────────────────────────────────────────────────────────────────
step "Step 6/11 — Grafana provisioning"
mkdir -p /etc/grafana/provisioning/datasources
mkdir -p /etc/grafana/provisioning/dashboards
mkdir -p /var/lib/grafana/dashboards

# Datasource
cat > /etc/grafana/provisioning/datasources/influxdb.yaml << GRAFANA_DS_EOF
apiVersion: 1
datasources:
  - name: InfluxDB
    type: influxdb
    uid: home-influxdb
    access: proxy
    url: http://localhost:8086
    database: ${INFLUX_DB}
    isDefault: true
    editable: true
GRAFANA_DS_EOF

# Dashboard provider
cat > /etc/grafana/provisioning/dashboards/home.yaml << GRAFANA_PROV_EOF
apiVersion: 1
providers:
  - name: Home Monitoring
    folder: Home
    type: file
    disableDeletion: false
    editable: true
    options:
      path: /var/lib/grafana/dashboards
GRAFANA_PROV_EOF

cp "${MONITORING_DIR}/dashboards/isp_dashboard.json" /var/lib/grafana/dashboards/
chown -R grafana:grafana /var/lib/grafana/dashboards

systemctl restart grafana-server
info "Grafana restarted with ISP dashboard."

# ─────────────────────────────────────────────────────────────────────────────
# STEP 7 — Systemd services (daemons only: ping-monitor, http-check)
# ─────────────────────────────────────────────────────────────────────────────
step "Step 7/11 — Systemd services"
for SERVICE in ping-monitor http-check; do
    SRC="${MONITORING_DIR}/services/${SERVICE}.service"
    DST="/etc/systemd/system/${SERVICE}.service"

    if [[ ! -f "${SRC}" ]]; then
        err "Service file not found: ${SRC}"
        continue
    fi

    sed \
        -e "s|__MONITORING_DIR__|${MONITORING_DIR}|g" \
        -e "s|__PI_USER__|${PI_USER}|g" \
        "${SRC}" > "${DST}"

    info "Installed ${DST}"
done

systemctl daemon-reload
systemctl enable ping-monitor http-check
systemctl start  ping-monitor http-check

# ─────────────────────────────────────────────────────────────────────────────
# STEP 8 — Cron jobs (speedtest, health-check, weekly-report)
# ─────────────────────────────────────────────────────────────────────────────
step "Step 8/11 — Cron jobs"
CRON_FILE="/etc/cron.d/home-monitoring"
VENV_PYTHON="${MONITORING_DIR}/venv/bin/python"
LOG="${MONITORING_DIR}/logs"

cat > "${CRON_FILE}" << CRON_EOF
# Home ISP Monitor — scheduled tasks
# Speedtest every 3 hours
0 */3 * * * ${PI_USER} ${VENV_PYTHON} ${MONITORING_DIR}/isp/speedtest_runner.py >> ${LOG}/speedtest.log 2>&1
# Health check every 5 minutes
*/5 * * * * ${PI_USER} ${VENV_PYTHON} ${MONITORING_DIR}/health_check.py >> ${LOG}/health_check.log 2>&1
# Weekly ISP quality report — Monday 07:00
0 7 * * 1 ${PI_USER} ${VENV_PYTHON} ${MONITORING_DIR}/reports/weekly_report.py >> ${LOG}/weekly_report.log 2>&1
CRON_EOF
chmod 644 "${CRON_FILE}"
info "Cron jobs written to ${CRON_FILE}"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 9 — Convenience CLI commands
# ─────────────────────────────────────────────────────────────────────────────
step "Step 9/11 — Installing convenience commands"

cat > /usr/local/bin/speedtest-now << CMD_EOF
#!/bin/bash
echo "Running speedtest..."
${MONITORING_DIR}/venv/bin/python ${MONITORING_DIR}/isp/speedtest_runner.py
CMD_EOF

cat > /usr/local/bin/report-now << CMD_EOF
#!/bin/bash
echo "Sending weekly report..."
${MONITORING_DIR}/venv/bin/python ${MONITORING_DIR}/reports/weekly_report.py
CMD_EOF

cat > /usr/local/bin/monitor-status << CMD_EOF
#!/bin/bash
echo ""
echo "=== Service Status ==="
for SVC in influxdb grafana-server ping-monitor http-check; do
    if systemctl is-active --quiet "\${SVC}"; then
        echo "  [UP]   \${SVC}"
    else
        echo "  [DOWN] \${SVC}"
    fi
done
echo ""
echo "=== Last Speedtest ==="
influx -database home_monitoring -execute \
    "SELECT download_mbps, upload_mbps, ping_ms FROM speedtest ORDER BY time DESC LIMIT 1" \
    2>/dev/null || echo "  No data yet"
echo ""
echo "=== Recent Outages ==="
influx -database home_monitoring -execute \
    "SELECT duration_seconds FROM outage_events ORDER BY time DESC LIMIT 3" \
    2>/dev/null || echo "  No outages recorded"
echo ""
CMD_EOF

cat > /usr/local/bin/menu << CMD_EOF
#!/bin/bash
while true; do
    echo ""
    echo "=== Monitoring Menu ==="
    echo "  1) speedtest-now"
    echo "  2) report-now"
    echo "  3) monitor-status"
    echo "  4) Exit"
    echo ""
    read -rp "Choose [1-4]: " choice
    case \$choice in
        1) speedtest-now ;;
        2) report-now ;;
        3) monitor-status ;;
        4) break ;;
        *) echo "Invalid choice" ;;
    esac
done
CMD_EOF

chmod +x /usr/local/bin/speedtest-now \
         /usr/local/bin/report-now \
         /usr/local/bin/monitor-status \
         /usr/local/bin/menu

info "Convenience commands installed: speedtest-now, report-now, monitor-status, menu"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 10 — InfluxDB 90-day retention policy
# ─────────────────────────────────────────────────────────────────────────────
step "Step 10/11 — InfluxDB retention policy"
influx -execute "CREATE RETENTION POLICY \"ninety_days\" ON \"${INFLUX_DB}\" DURATION 90d REPLICATION 1 DEFAULT" 2>/dev/null || \
    warn "Could not set retention policy — can be set manually later."
info "90-day retention policy set as default."

# ─────────────────────────────────────────────────────────────────────────────
# STEP 11 — Final status check
# ─────────────────────────────────────────────────────────────────────────────
step "Step 11/11 — Status check"
sleep 3
for SVC in influxdb grafana-server ping-monitor http-check; do
    if systemctl is-active --quiet "${SVC}"; then
        echo -e "  ${GREEN}●${NC} ${SVC}  running"
    else
        echo -e "  ${RED}●${NC} ${SVC}  NOT running"
    fi
done

echo ""
echo -e "  Cron jobs:"
echo -e "  ${GREEN}●${NC} speedtest      every 3 hours"
echo -e "  ${GREEN}●${NC} health-check   every 5 minutes"
echo -e "  ${GREEN}●${NC} weekly-report  Monday 07:00"

# ── Done banner ─────────────────────────────────────────────────────────────
PI_IP=$(hostname -I | awk '{print $1}')

echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${GREEN}║     Installation Complete!                   ║${NC}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Grafana  :  ${BLUE}http://${PI_IP}:3000${NC}  (admin / admin)"
echo -e "  Pi-hole  :  ${BLUE}http://${PI_IP}/admin${NC}"
echo ""
echo -e "${YELLOW}  NEXT STEPS:${NC}"
echo -e "  1. Edit ${MONITORING_DIR}/config.yaml:"
echo -e "     - Add Telegram bot_token and chat_id"
echo -e "     - Add Gmail app_password"
echo -e "  2. Point Deco DNS to this Pi:"
echo -e "     Deco app > More > Advanced > DHCP Server"
echo -e "     DNS (Primary) > ${PI_IP}"
echo -e "  3. Restart services after editing config:"
echo -e "     sudo systemctl restart ping-monitor http-check"
echo ""
echo -e "  ${BLUE}Manual commands (run from anywhere):${NC}"
echo -e "    speedtest-now    — run a speed test immediately"
echo -e "    report-now       — send weekly report immediately"
echo -e "    monitor-status   — show service status + last results"
echo -e "    menu             — interactive menu"
echo ""
echo -e "  ${BLUE}Logs:${NC}"
echo -e "    journalctl -u ping-monitor -f"
echo -e "    journalctl -u http-check -f"
echo -e "    cat ${LOG}/health_check.log"
echo ""
