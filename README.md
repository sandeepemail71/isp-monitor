# Home ISP Monitor

Plug-and-play ISP monitoring stack for a dedicated Raspberry Pi 3B.
Tracks Airtel Fiber quality — latency, speed, outages, and HTTP connectivity.

---

## What You Get

| Feature | Details |
|---|---|
| **ISP latency** | Continuous ping to 8.8.8.8 / 1.1.1.1 every 10 s |
| **Outage detection** | Detects full outages, logs duration, Telegram alert |
| **Speed tests** | Every 3 hours via Ookla speedtest CLI |
| **HTTP checks** | google.com / cloudflare.com / github.com every 60 s |
| **Weekly ISP report** | Monday 7 AM → HTML email (great for ISP complaints) |
| **Pi-hole DNS** | Network-wide ad blocking + per-device DNS query log |
| **Health checks** | InfluxDB, services, disk space checked every 5 min |
| **Grafana** | ISP dashboard: latency, speed, uptime, outage timeline |

---

## Architecture

```
Internet (Airtel Fiber)
        |
    [ONT/Router]
        |
 [Deco M4/E4 mesh]  <-- DNS pointed to Pi IP
        |
  [Pi 3B — static IP]
  +------------------------------+
  |  Pi-hole    :53 / :80        |
  |  InfluxDB   :8086 (90d ret.) |
  |  Grafana    :3000            |
  |  Daemons: ping-monitor,      |
  |           http-check         |
  |  Cron: speedtest,            |
  |        health-check, report  |
  +------------------------------+
         |                |
   [Telegram Bot]   [Gmail SMTP]
```

---

## Prerequisites

- Raspberry Pi 3B running Raspberry Pi OS Lite (64-bit)
- Pi connected via **Ethernet** (`eth0`)
- Pi has a **static IP** assigned in your router/Deco app
- Internet access from the Pi

---

## Setup — Option A: Plug and Play (recommended)

Flash → run one command on Mac → insert SD → done. Pi sets itself up automatically.

### Step 1 — Flash Pi OS with Raspberry Pi Imager

1. Download [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
2. Choose **Raspberry Pi OS Lite (64-bit)**
3. Click the **gear icon** (advanced options) and set:
   - Hostname: `wan-monitoring`
   - Enable SSH: **password authentication**
   - Username: `dev`, Password: `<your choice>`
4. Flash to SD card — keep it mounted after flashing

### Step 2 — Fill in config.yaml on your Mac

Edit `config.yaml` with your credentials (only needs to be done once):

```yaml
telegram:
  bot_token: "123456:ABCdef..."    # from @BotFather
  chat_id: "987654321"             # from @userinfobot

email:
  enabled: true
  sender: "you@gmail.com"
  app_password: "xxxx xxxx xxxx xxxx"  # Gmail > App Passwords
  recipient: "you@gmail.com"
```

### Step 3 — Prepare the SD card

```bash
./prepare-sd.sh
```

Auto-detects the mounted SD card. Copies all project files, `config.yaml`, and `firstrun.sh` onto the boot partition. Updates `cmdline.txt` to trigger auto-setup on first boot.

> **Tip:** You can also edit `config.yaml` directly on the SD card after this step:
> ```bash
> open /Volumes/bootfs/config.yaml
> ```

### Step 4 — Insert SD card and power on

That's it. The Pi will:
1. Boot and run `firstrun.sh` automatically
2. Install InfluxDB, Grafana, Pi-hole, Python venv, services, cron jobs
3. Send a Telegram message when ready (~5-15 minutes)

```
✅ wan-monitoring is ready!
IP: 192.168.68.116
Grafana: http://192.168.68.116:3000
Pi-hole: http://192.168.68.116/admin
```

If setup fails, check the log:
```bash
ssh dev@<PI_IP> 'cat /var/log/firstrun.log'
```

---

## Setup — Option B: Manual

Use this if you already have a Pi running and just want to deploy the stack.

### Step 1 — Deploy to Pi

```bash
./deploy.sh <PI_IP>
# or with custom user:
./deploy.sh <PI_IP> pi
```

This copies all files and runs `install.sh` on the Pi automatically.

### Step 2 — Edit config.yaml on the Pi

```bash
ssh dev@<PI_IP>
nano /home/dev/monitoring/config.yaml
```

### Step 3 — Restart services

```bash
sudo systemctl restart ping-monitor http-check
```

---

## After Setup

### Point Deco DNS to Pi

1. Open **Deco app**
2. **More > Advanced > DHCP Server**
3. Set **Primary DNS** to your Pi's IP
4. Save and wait ~60 s for devices to pick up new DNS

### Access URLs

| Service | URL | Credentials |
|---|---|---|
| Grafana | `http://<PI_IP>:3000` | admin / admin (change on first login) |
| Pi-hole | `http://<PI_IP>/admin` | set with `pihole -a -p` |
| InfluxDB | `http://<PI_IP>:8086` | none (local only) |

---

## Services & Cron

### Systemd services (daemons)

```bash
# Status
systemctl status ping-monitor http-check

# Live logs
journalctl -u ping-monitor -f
journalctl -u http-check -f

# Restart
sudo systemctl restart ping-monitor http-check
```

### Cron jobs

```bash
cat /etc/cron.d/home-monitoring
```

| Job | Schedule | Script |
|---|---|---|
| Speedtest | Every 3 hours | `isp/speedtest_runner.py` |
| Health check | Every 5 min | `health_check.py` |
| Weekly report | Monday 07:00 | `reports/weekly_report.py` |

### Manual runs (convenience commands)

These commands are installed to `/usr/local/bin/` and work from anywhere on the Pi:

| Command | What it does |
|---|---|
| `speedtest-now` | Run a speed test immediately, write to InfluxDB, alert if slow |
| `report-now` | Send the weekly ISP report email immediately |
| `monitor-status` | Service health + last speedtest + recent outages |
| `menu` | Interactive menu for all of the above |

---

## File Structure

```
.
├── prepare-sd.sh           # Mac: prepare SD card for plug-and-play setup
├── firstrun.sh             # Pi: runs on first boot, installs everything
├── deploy.sh               # Mac: deploy to an existing Pi over SSH
├── install.sh              # Pi: one-shot installer (called by firstrun.sh or manually)
├── config.yaml             # All settings — edit this before deploying
├── common.py               # Shared: config loader, InfluxDB client, alerts
├── health_check.py         # Cron: checks InfluxDB, services, disk space
├── requirements.txt
├── isp/
│   ├── ping_monitor.py     # Daemon: latency + outage detection
│   ├── speedtest_runner.py # Cron: Ookla speed test every 3h
│   └── http_check.py       # Daemon: HTTP connectivity checks
├── reports/
│   └── weekly_report.py    # Cron: weekly ISP summary email
├── services/               # Systemd unit templates
└── dashboards/             # Grafana dashboard JSON
```

---

## InfluxDB Queries

```bash
influx -database home_monitoring

# Last 10 pings
SELECT * FROM ping_stats ORDER BY time DESC LIMIT 10;

# Outage history
SELECT * FROM outage_events ORDER BY time DESC;

# Speed history
SELECT download_mbps, upload_mbps FROM speedtest ORDER BY time DESC LIMIT 20;
```

---

## Data Retention

InfluxDB is configured with a **90-day retention policy** automatically during install. Data older than 90 days is purged to protect the SD card.

---

## Troubleshooting

**firstrun.sh did not run / Pi did not set itself up**
```bash
# Check the log after SSH-ing in
cat /var/log/firstrun.log

# Check if cmdline.txt still has the trigger (should be removed after successful run)
cat /boot/firmware/cmdline.txt
```

**Service won't start**
```bash
journalctl -u ping-monitor --no-pager -n 50
```

**InfluxDB not receiving data**
```bash
systemctl status influxdb
sudo systemctl start influxdb
```

**Grafana dashboard shows "No data"**
- Check time range (top-right) — set to "Last 24 hours"
- Settings > Data Sources > InfluxDB > Test
- Verify services are running: `monitor-status`

**Weekly email not arriving**
```bash
/home/dev/monitoring/venv/bin/python /home/dev/monitoring/reports/weekly_report.py
cat /home/dev/monitoring/logs/weekly_report.log
```
Ensure Gmail App Password is set (not your regular password).

**Pi-hole conflicts on port 80**
Pi-hole uses lighttpd on port 80, Grafana on 3000 — no conflict by default.
```bash
sudo ss -tlnp | grep :80
```

**Speedtest results much lower than expected**
- Check which Deco node the Pi is connected to
- Run `iperf3` between Pi and Mac to test LAN throughput
- If LAN speed is low, Pi is on a satellite node — move Ethernet to main Deco node
