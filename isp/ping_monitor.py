#!/usr/bin/env python3
"""Continuous ISP latency and outage daemon.

Pings configured targets every N seconds, writes to InfluxDB.
Detects full outages (all targets unreachable) and records events.
Sends rate-limited Telegram alerts on outage start/end.

Run as systemd service: ping-monitor.service
"""

import os
import re
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import get_influx_client, get_logger, load_config, rate_limited_alert

log = get_logger("ping_monitor")
_running = True


def _handle_signal(sig, frame):
    global _running
    log.info("Shutdown signal received.")
    _running = False


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def ping_host(host, count=3, timeout=2):
    """Ping host. Returns (avg_rtt_ms, packet_loss_pct).
    Returns (None, 100.0) on failure."""
    try:
        result = subprocess.run(
            ["ping", "-c", str(count), "-W", str(timeout), host],
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout

        loss_match = re.search(r"(\d+(?:\.\d+)?)% packet loss", output)
        packet_loss = float(loss_match.group(1)) if loss_match else 100.0

        rtt_match = re.search(
            r"rtt min/avg/max(?:/mdev)? = [\d.]+/([\d.]+)/", output
        )
        avg_rtt = float(rtt_match.group(1)) if rtt_match else None

        return avg_rtt, packet_loss
    except Exception as exc:
        log.debug(f"ping {host} error: {exc}")
        return None, 100.0


def write_ping(client, target, rtt_ms, packet_loss_pct, is_up):
    fields = {"packet_loss_pct": packet_loss_pct, "is_up": int(is_up)}
    if rtt_ms is not None:
        fields["rtt_ms"] = rtt_ms
    client.write_points([{
        "measurement": "ping_stats",
        "tags": {"target": target},
        "fields": fields,
    }])


def main():
    config = load_config()
    isp = config["isp"]
    targets = isp["ping_targets"]
    interval = isp.get("ping_interval_seconds", 10)
    loss_threshold = isp.get("ping_loss_threshold_pct", 20)

    client = get_influx_client()
    outage_start = None

    log.info(f"Ping monitor started | targets={targets} interval={interval}s")

    while _running:
        cycle_start = time.time()
        all_down = True

        for target in targets:
            rtt, loss = ping_host(target)
            is_up = rtt is not None and loss < loss_threshold
            if is_up:
                all_down = False
            try:
                write_ping(client, target, rtt, loss, is_up)
            except Exception as exc:
                log.error(f"InfluxDB write failed: {exc}")

        now = time.time()

        # ── Outage detection ─────────────────────────────────────────
        if all_down and outage_start is None:
            outage_start = now
            log.warning(f"OUTAGE STARTED at {time.strftime('%Y-%m-%d %H:%M:%S')}")
            rate_limited_alert(
                "outage_start",
                "🔴 <b>Internet outage detected!</b>\n"
                f"All targets unreachable.\n"
                f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}",
                cooldown_seconds=300,
            )

        elif not all_down and outage_start is not None:
            duration = int(now - outage_start)
            mins, secs = divmod(duration, 60)
            log.warning(f"OUTAGE ENDED | duration={duration}s")
            try:
                client.write_points([{
                    "measurement": "outage_events",
                    "fields": {"duration_seconds": duration},
                }])
            except Exception as exc:
                log.error(f"Failed to write outage event: {exc}")
            rate_limited_alert(
                "outage_end",
                "✅ <b>Internet restored!</b>\n"
                f"Outage duration: {mins}m {secs}s",
                cooldown_seconds=0,
            )
            outage_start = None

        elapsed = time.time() - cycle_start
        time.sleep(max(0.0, interval - elapsed))

    log.info("Ping monitor stopped.")


if __name__ == "__main__":
    main()
