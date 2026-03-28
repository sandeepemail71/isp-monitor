#!/usr/bin/env python3
"""Weekly ISP quality report — email + Telegram confirmation.

Queries InfluxDB for 7 days of metrics, builds HTML + plain-text email,
sends via Gmail SMTP.

Called via cron every Monday at 07:00, or run manually any time.
"""

import os
import smtplib
import socket
import sys
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import get_influx_client, get_logger, load_config, send_telegram, get_pi_name

log = get_logger("weekly_report")


def _get_pi_ip():
    """Auto-detect the Pi's LAN IP. Falls back to config pi_ip if detection fails."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return load_config().get("pi_ip", "localhost")


# ── InfluxDB queries ─────────────────────────────────────────────────────────

def _q(client, query):
    try:
        return list(client.query(query).get_points())
    except Exception as exc:
        log.error(f"Query error: {exc}")
        return []


def get_speed_stats(client):
    points = _q(client, """
        SELECT download_mbps, upload_mbps, time
        FROM speedtest WHERE time > now() - 7d ORDER BY time ASC
    """)
    if not points:
        return None

    downloads = [p["download_mbps"] for p in points if p.get("download_mbps") is not None]
    uploads = [p["upload_mbps"] for p in points if p.get("upload_mbps") is not None]
    if not downloads:
        return None

    worst = min(points, key=lambda p: p.get("download_mbps") or 999)
    worst_time = (worst.get("time") or "")[:16].replace("T", " ")

    return {
        "avg_dl": sum(downloads) / len(downloads),
        "min_dl": min(downloads),
        "max_dl": max(downloads),
        "avg_ul": sum(uploads) / len(uploads) if uploads else 0,
        "min_ul": min(uploads) if uploads else 0,
        "sample_count": len(downloads),
        "worst_time": worst_time or "N/A",
    }


def get_outage_stats(client):
    points = _q(client, """
        SELECT duration_seconds, time
        FROM outage_events WHERE time > now() - 7d ORDER BY time ASC
    """)
    if not points:
        return {"total_minutes": 0.0, "count": 0, "longest_secs": 0, "longest_time": "N/A"}

    durations = [p.get("duration_seconds", 0) for p in points]
    longest_pt = max(points, key=lambda p: p.get("duration_seconds", 0))
    return {
        "total_minutes": sum(durations) / 60,
        "count": len(points),
        "longest_secs": max(durations),
        "longest_time": (longest_pt.get("time") or "")[:16].replace("T", " ") or "N/A",
    }


def get_latency_stats(client):
    agg = _q(client, """
        SELECT MEAN(rtt_ms) AS avg_rtt, MAX(rtt_ms) AS peak_rtt
        FROM ping_stats WHERE time > now() - 7d AND "target" = '8.8.8.8'
    """)
    loss_pts = _q(client, """
        SELECT COUNT(packet_loss_pct) AS n
        FROM ping_stats WHERE time > now() - 7d AND packet_loss_pct > 5
    """)
    hourly = _q(client, """
        SELECT MEAN(rtt_ms) AS avg_rtt
        FROM ping_stats WHERE time > now() - 7d AND "target" = '8.8.8.8'
        GROUP BY time(1h)
    """)

    avg_rtt = (agg[0].get("avg_rtt") or 0) if agg else 0
    peak_rtt = (agg[0].get("peak_rtt") or 0) if agg else 0
    loss_events = (loss_pts[0].get("n") or 0) if loss_pts else 0

    worst_h = max(hourly, key=lambda p: p.get("avg_rtt") or 0) if hourly else None
    worst_hour_time = (worst_h.get("time") or "")[:16].replace("T", " ") if worst_h else "N/A"
    worst_hour_rtt = (worst_h.get("avg_rtt") or 0) if worst_h else 0

    return {
        "avg_rtt": avg_rtt,
        "peak_rtt": peak_rtt,
        "loss_events": int(loss_events),
        "worst_hour_time": worst_hour_time,
        "worst_hour_rtt": worst_hour_rtt,
    }


def get_http_stats(client):
    points = _q(client, """
        SELECT is_up, url, time FROM http_check WHERE time > now() - 7d
    """)
    by_url = {}
    for p in points:
        url = p.get("url", "unknown")
        if url not in by_url:
            by_url[url] = {"total": 0, "up": 0}
        by_url[url]["total"] += 1
        by_url[url]["up"] += int(p.get("is_up", 0))

    failures = [
        {"url": url, "failures": v["total"] - v["up"]}
        for url, v in by_url.items()
        if v["total"] - v["up"] > 0
    ]
    return {
        "total_failures": sum(f["failures"] for f in failures),
        "by_url": failures,
    }


# ── Email builder ────────────────────────────────────────────────────────────

def _td(label, value):
    return f"<tr><td>{label}</td><td>{value}</td></tr>\n"


def build_html(week_label, speed, outages, latency, http):
    pi_ip = _get_pi_ip()
    year = datetime.now().year

    if speed:
        speed_rows = (
            _td("Avg Download", f"<b>{speed['avg_dl']:.1f} Mbps</b>")
            + _td("Min Download", f"{speed['min_dl']:.1f} Mbps (worst at {speed['worst_time']})")
            + _td("Max Download", f"{speed['max_dl']:.1f} Mbps")
            + _td("Avg Upload", f"<b>{speed['avg_ul']:.1f} Mbps</b>")
            + _td("Min Upload", f"{speed['min_ul']:.1f} Mbps")
            + _td("Tests Run", str(speed["sample_count"]))
        )
    else:
        speed_rows = "<tr><td colspan='2'>No speed data collected this week.</td></tr>"

    if outages["count"] > 0:
        outage_rows = (
            _td("Total Downtime", f"<b>{outages['total_minutes']:.1f} min</b> across {outages['count']} event(s)")
            + _td("Longest Outage", f"{outages['longest_secs']} s at {outages['longest_time']}")
        )
    else:
        outage_rows = "<tr><td colspan='2'>No outages recorded this week!</td></tr>"

    http_rows = _td(
        "HTTP Check Failures",
        "None" if http["total_failures"] == 0 else f"{http['total_failures']} total",
    )
    for f in http["by_url"]:
        http_rows += _td(f["url"], f"{f['failures']} failures")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
  body    {{ font-family: Arial, sans-serif; color: #333; max-width: 640px; margin: 0 auto; padding: 8px; }}
  h1      {{ background: #1a73e8; color: #fff; padding: 14px 18px; margin: 0; font-size: 17px; border-radius: 4px 4px 0 0; }}
  h2      {{ color: #1a73e8; border-bottom: 2px solid #1a73e8; padding-bottom: 4px; margin: 22px 0 6px; font-size: 14px; text-transform: uppercase; letter-spacing: .5px; }}
  table   {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  td      {{ padding: 7px 10px; border-bottom: 1px solid #eee; }}
  td:first-child {{ color: #555; width: 48%; }}
  .footer {{ font-size: 11px; color: #aaa; margin-top: 28px; padding-top: 12px; border-top: 1px solid #eee; }}
</style>
</head>
<body>
<h1>ISP Quality Report — {week_label}, {year}</h1>

<h2>Speed (Airtel Fiber)</h2>
<table>{speed_rows}</table>

<h2>Outages</h2>
<table>{outage_rows}</table>

<h2>Latency &amp; Packet Loss</h2>
<table>
  {_td("Avg Latency (8.8.8.8)", f"<b>{latency['avg_rtt']:.1f} ms</b>")}
  {_td("Peak Latency", f"{latency['peak_rtt']:.1f} ms")}
  {_td("Packet Loss Events (&gt;5%)", str(latency['loss_events']))}
  {_td("Worst Hour", f"{latency['worst_hour_time']} (avg {latency['worst_hour_rtt']:.1f} ms)")}
</table>

<h2>Connectivity Checks</h2>
<table>{http_rows}</table>

<div class="footer">
  Generated by Home ISP Monitor &bull;
  Grafana: <a href="http://{pi_ip}:3000">http://{pi_ip}:3000</a> &bull;
  Pi-hole: <a href="http://{pi_ip}/admin">http://{pi_ip}/admin</a>
</div>
</body>
</html>"""


def build_text(week_label, speed, outages, latency, http):
    lines = [f"ISP Quality Report — {week_label}, {datetime.now().year}", "=" * 50]

    if speed:
        lines += [
            "\nSPEED (Airtel Fiber)",
            f"  Avg Download : {speed['avg_dl']:.1f} Mbps",
            f"  Min Download : {speed['min_dl']:.1f} Mbps (worst at {speed['worst_time']})",
            f"  Avg Upload   : {speed['avg_ul']:.1f} Mbps",
            f"  Min Upload   : {speed['min_ul']:.1f} Mbps",
            f"  Tests Run    : {speed['sample_count']}",
        ]
    else:
        lines.append("\nSPEED: No data collected this week.")

    lines += [
        "\nOUTAGES",
        f"  Total Downtime : {outages['total_minutes']:.1f} min across {outages['count']} event(s)",
        f"  Longest        : {outages['longest_secs']} s at {outages['longest_time']}",
        "\nLATENCY & PACKET LOSS",
        f"  Avg Latency (8.8.8.8) : {latency['avg_rtt']:.1f} ms",
        f"  Peak Latency          : {latency['peak_rtt']:.1f} ms",
        f"  Packet Loss Events    : {latency['loss_events']}",
        f"  Worst Hour            : {latency['worst_hour_time']}",
        "\nHTTP CHECKS",
        f"  Failures : {http['total_failures']}",
    ]

    pi_ip = _get_pi_ip()
    lines += [
        f"\nGrafana : http://{pi_ip}:3000",
        f"Pi-hole : http://{pi_ip}/admin",
    ]
    return "\n".join(lines)


# ── Email sender ─────────────────────────────────────────────────────────────

def send_email(config, subject, html_body, text_body):
    em = config.get("email", {})
    if not em.get("enabled", False):
        log.warning("Email not enabled — skipping.")
        return
    app_pw = em.get("app_password", "")
    if not app_pw or "YOUR_" in app_pw:
        log.warning("Email app_password not configured — skipping.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = em["sender"]
    msg["To"] = em["recipient"]
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(em["smtp_host"], em["smtp_port"]) as srv:
            srv.starttls()
            srv.login(em["sender"], app_pw)
            srv.sendmail(em["sender"], em["recipient"], msg.as_string())
        log.info(f"Report sent to {em['recipient']}")
    except Exception as exc:
        log.error(f"Failed to send email: {exc}")


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    config = load_config()
    client = get_influx_client()

    log.info("Generating weekly ISP quality report...")

    week_start = (datetime.now() - timedelta(days=7)).strftime("%b %-d")
    week_end = datetime.now().strftime("%b %-d")
    week_label = f"{week_start}–{week_end}"

    speed = get_speed_stats(client)
    outages = get_outage_stats(client)
    latency = get_latency_stats(client)
    http = get_http_stats(client)

    html_body = build_html(week_label, speed, outages, latency, http)
    text_body = build_text(week_label, speed, outages, latency, http)

    downtime_str = f"{outages['total_minutes']:.0f}min" if outages["count"] else "0 outages"
    pi_name = get_pi_name()
    subject = f"[{pi_name}] ISP Report {week_label} | Downtime: {downtime_str}"

    send_email(config, subject, html_body, text_body)

    send_telegram(
        f"<b>[{pi_name}] Weekly ISP report sent</b>\n"
        f"Period: {week_label}\n"
        f"Downtime: {downtime_str}"
    )

    log.info("Weekly report done.")


if __name__ == "__main__":
    main()
