#!/usr/bin/env python3
"""Run a speed test and write results to InfluxDB.

Called via cron every 3 hours. Uses the official Ookla speedtest CLI.
Sends a rate-limited Telegram alert if download speed falls below
the configured threshold.
"""

import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import get_influx_client, get_logger, load_config, rate_limited_alert

log = get_logger("speedtest")


def _speedtest_bin():
    """Return the path to the Ookla speedtest binary."""
    path = shutil.which("speedtest")
    if path:
        return path
    log.error("Ookla speedtest CLI not found — install via: "
              "curl -s https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | sudo bash "
              "&& sudo apt-get install -y speedtest")
    return None


def run_speedtest(server_id=None):
    """Run Ookla speedtest and return (download_mbps, upload_mbps, ping_ms, server_name) or None."""
    bin_path = _speedtest_bin()
    if not bin_path:
        return None

    cmd = [bin_path, "--format=json", "--accept-license", "--accept-gdpr"]
    if server_id:
        cmd += ["--server-id", str(server_id)]

    log.info(f"Running speedtest... (server={server_id or 'auto'})")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=180,
        )
        if result.returncode != 0:
            log.error(f"speedtest exited {result.returncode}: {result.stderr.strip()}")
            return None

        data = json.loads(result.stdout)

        # Ookla CLI: bandwidth is in bytes/sec, ping latency in ms
        download_mbps = data["download"]["bandwidth"] * 8 / 1_000_000
        upload_mbps = data["upload"]["bandwidth"] * 8 / 1_000_000
        ping_ms = data["ping"]["latency"]
        server_name = data.get("server", {}).get("name", "Unknown")

        return download_mbps, upload_mbps, ping_ms, server_name

    except subprocess.TimeoutExpired:
        log.error("speedtest timed out after 180s")
        return None
    except (json.JSONDecodeError, KeyError) as exc:
        log.error(f"Could not parse speedtest output: {exc}")
        return None
    except FileNotFoundError:
        log.error("speedtest binary not found")
        return None


def main():
    config = load_config()
    client = get_influx_client()

    server_id = config["isp"].get("speedtest_server_id") or None
    result = run_speedtest(server_id)
    if result is None:
        sys.exit(1)

    download_mbps, upload_mbps, ping_ms, server_name = result

    log.info(
        f"Down={download_mbps:.1f} Mbps  Up={upload_mbps:.1f} Mbps  "
        f"Ping={ping_ms:.1f} ms  Server={server_name}"
    )

    try:
        client.write_points([{
            "measurement": "speedtest",
            "tags": {"server": server_name},
            "fields": {
                "download_mbps": download_mbps,
                "upload_mbps": upload_mbps,
                "ping_ms": ping_ms,
            },
        }])
    except Exception as exc:
        log.error(f"InfluxDB write failed: {exc}")
        sys.exit(1)

    threshold = config["isp"].get("speed_alert_threshold_mbps", 10.0)
    if download_mbps < threshold:
        rate_limited_alert(
            "speed_degraded",
            "⚠️ <b>Speed degraded!</b>\n"
            f"Download: {download_mbps:.1f} Mbps (threshold: {threshold})\n"
            f"Upload: {upload_mbps:.1f} Mbps\n"
            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            cooldown_seconds=3600,
        )
        log.warning(f"Speed below threshold: {download_mbps:.1f} Mbps")

    log.info("Speedtest complete.")


if __name__ == "__main__":
    main()
