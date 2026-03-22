#!/usr/bin/env python3
"""Infrastructure health monitor — cron job every 5 minutes.

Checks: InfluxDB reachable, systemd services running, disk space.
Sends a rate-limited Telegram alert if any check fails.
"""

import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import get_logger, rate_limited_alert

log = get_logger("health_check")

SERVICES = ["ping-monitor", "http-check"]


def check_influxdb():
    """Returns True if InfluxDB responds to ping."""
    import requests
    try:
        r = requests.get("http://localhost:8086/ping", timeout=5)
        return r.status_code == 204
    except Exception:
        return False


def check_services():
    """Returns list of services that are NOT running."""
    down = []
    for svc in SERVICES:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", svc],
            capture_output=True,
        )
        if result.returncode != 0:
            down.append(svc)
    return down


def check_disk():
    """Returns (pct_free, is_low). is_low=True if free < 10%."""
    usage = shutil.disk_usage("/")
    pct_free = (usage.free / usage.total) * 100
    return pct_free, pct_free < 10


def main():
    issues = []

    if not check_influxdb():
        issues.append("InfluxDB unreachable")

    down_svcs = check_services()
    if down_svcs:
        issues.append(f"Services down: {', '.join(down_svcs)}")

    pct_free, low_disk = check_disk()
    if low_disk:
        issues.append(f"Disk space low: {pct_free:.1f}% free")

    if issues:
        msg = "<b>Health Check Alert</b>\n" + "\n".join(f"- {i}" for i in issues)
        rate_limited_alert("health_check", msg, cooldown_seconds=3600)
        log.warning(f"Health issues: {issues}")
    else:
        log.info("All checks passed.")


if __name__ == "__main__":
    main()
