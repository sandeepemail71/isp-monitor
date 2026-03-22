#!/usr/bin/env python3
"""HTTP connectivity health check daemon.

GETs configured URLs every N seconds and records response time / status
into InfluxDB. Sends alert when ALL URLs fail simultaneously.

Run as systemd service: http-check.service
"""

import os
import signal
import sys
import time

import requests as req

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import get_influx_client, get_logger, load_config, rate_limited_alert

log = get_logger("http_check")
_running = True


def _handle_signal(sig, frame):
    global _running
    _running = False


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def check_url(url, timeout=10):
    """HTTP GET url. Returns (response_ms, status_code, is_up)."""
    start = time.monotonic()
    try:
        resp = req.get(
            url, timeout=timeout, allow_redirects=True,
            headers={"User-Agent": "HomeMonitor/1.0"},
        )
        elapsed_ms = (time.monotonic() - start) * 1000
        is_up = resp.status_code < 500
        return elapsed_ms, resp.status_code, is_up
    except req.exceptions.Timeout:
        return None, 0, False
    except req.exceptions.ConnectionError:
        return None, 0, False
    except Exception as exc:
        log.debug(f"HTTP error {url}: {exc}")
        return None, 0, False


def main():
    config = load_config()
    urls = config["isp"].get("http_check_urls", ["https://www.google.com"])
    interval = config["isp"].get("http_check_interval_seconds", 60)
    client = get_influx_client()

    http_outage_active = False

    log.info(f"HTTP check daemon started | urls={urls} interval={interval}s")

    while _running:
        cycle_start = time.monotonic()
        all_failed = True

        for url in urls:
            response_ms, status_code, is_up = check_url(url)
            if is_up:
                all_failed = False

            fields = {"status_code": status_code, "is_up": int(is_up)}
            if response_ms is not None:
                fields["response_ms"] = response_ms

            try:
                client.write_points([{
                    "measurement": "http_check",
                    "tags": {"url": url},
                    "fields": fields,
                }])
            except Exception as exc:
                log.error(f"InfluxDB write failed: {exc}")

        # All-down detection
        if all_failed and not http_outage_active:
            http_outage_active = True
            rate_limited_alert(
                "http_outage",
                "🌐 <b>All HTTP checks failing!</b>\n"
                f"URLs: {', '.join(urls)}\n"
                f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}",
                cooldown_seconds=300,
            )
        elif not all_failed and http_outage_active:
            http_outage_active = False

        elapsed = time.monotonic() - cycle_start
        time.sleep(max(0.0, interval - elapsed))

    log.info("HTTP check daemon stopped.")


if __name__ == "__main__":
    main()
