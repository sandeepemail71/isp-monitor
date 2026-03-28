#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quick test for Telegram and Email alerts.

Usage (on Pi or Mac with venv active):
    python test_alerts.py            # test both
    python test_alerts.py telegram   # test Telegram only
    python test_alerts.py email      # test Email only
"""

import sys
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")

# ── fallback: try config.local.yaml if config.yaml not found ─────────────────
if not os.path.isfile(CONFIG_PATH):
    local = os.path.join(BASE_DIR, "config.local.yaml")
    if os.path.isfile(local):
        CONFIG_PATH = local

import socket
import yaml

with open(CONFIG_PATH, "r") as f:
    cfg = yaml.safe_load(f)

PI_NAME = cfg.get("name") or socket.gethostname()


# ─────────────────────────────────────────────────────────────────────────────
# Telegram
# ─────────────────────────────────────────────────────────────────────────────
def test_telegram():
    print("\n── Telegram ─────────────────────────────────")
    tg = cfg.get("telegram", {})

    if not tg.get("enabled", False):
        print("  SKIP  telegram.enabled = false in config")
        return

    token = tg.get("bot_token", "")
    chat_id = tg.get("chat_id", "")

    if not token or "YOUR_" in token:
        print("  FAIL  bot_token not set in config")
        return
    if not chat_id or "YOUR_" in chat_id:
        print("  FAIL  chat_id not set in config")
        return

    import requests
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(url, data={
            "chat_id": chat_id,
            "text": f"[{PI_NAME}] ISP Monitor test message — Telegram is working.",
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=15)
        if resp.ok:
            print("  OK    Message sent — check your Telegram")
        else:
            print(f"  FAIL  HTTP {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"  FAIL  {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Email
# ─────────────────────────────────────────────────────────────────────────────
def test_email():
    print("\n── Email ────────────────────────────────────")
    em = cfg.get("email", {})

    if not em.get("enabled", False):
        print("  SKIP  email.enabled = false in config")
        return

    sender   = em.get("sender", "")
    password = em.get("app_password", "")
    recipient = em.get("recipient", "")
    smtp_host = em.get("smtp_host", "smtp.gmail.com")
    smtp_port = em.get("smtp_port", 587)

    if not password or "YOUR_" in password:
        print("  FAIL  app_password not set in config")
        return
    if not sender or "you@" in sender:
        print("  FAIL  sender email not set in config")
        return

    import smtplib
    from email.mime.text import MIMEText

    msg = MIMEText(f"[{PI_NAME}] ISP Monitor test message — Email is working.")
    msg["Subject"] = f"[{PI_NAME}] ISP Monitor — Test Email"
    msg["From"]    = sender
    msg["To"]      = recipient

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as smtp:
            smtp.starttls()
            smtp.login(sender, password)
            smtp.sendmail(sender, [recipient], msg.as_string())
        print(f"  OK    Email sent to {recipient} — check your inbox")
    except Exception as e:
        print(f"  FAIL  {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
arg = sys.argv[1].lower() if len(sys.argv) > 1 else "both"

if arg in ("telegram", "both"):
    test_telegram()
if arg in ("email", "both"):
    test_email()

print()
