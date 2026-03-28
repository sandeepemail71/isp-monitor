#!/usr/bin/env python3
"""Shared utilities for the Home Network & ISP Monitor."""

import json
import logging
import os
import sys
import tempfile
import time

import yaml

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
LOG_DIR = os.path.join(BASE_DIR, "logs")
DATA_DIR = os.path.join(BASE_DIR, "data")
ALERT_STATE_PATH = os.path.join(DATA_DIR, ".alert_state.json")

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

# ── Cached singletons ───────────────────────────────────────────────────────
_config = None
_influx_client = None


def load_config():
    """Load and cache config.yaml. Exits if the file is missing."""
    global _config
    if _config is not None:
        return _config
    if not os.path.isfile(CONFIG_PATH):
        print(f"FATAL: {CONFIG_PATH} not found.", file=sys.stderr)
        sys.exit(1)
    with open(CONFIG_PATH, "r") as f:
        _config = yaml.safe_load(f)
    return _config


def get_influx_client():
    """Return a cached InfluxDB 1.x client."""
    global _influx_client
    if _influx_client is not None:
        return _influx_client
    from influxdb import InfluxDBClient

    cfg = load_config()["influxdb"]
    _influx_client = InfluxDBClient(
        host=cfg.get("host", "localhost"),
        port=cfg.get("port", 8086),
        database=cfg.get("database", "home_monitoring"),
    )
    return _influx_client


def get_logger(name):
    """Return a logger that writes to logs/{name}.log and stdout."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(os.path.join(LOG_DIR, f"{name}.log"))
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


def get_pi_name():
    """Return the name of this Pi from config (e.g. 'Pi-Home')."""
    import socket
    return load_config().get("name") or socket.gethostname()


def send_telegram(message):
    """Send an HTML-formatted Telegram message. Returns True on success."""
    cfg = load_config().get("telegram", {})
    if not cfg.get("enabled", False):
        return False
    token = cfg.get("bot_token", "")
    chat_id = cfg.get("chat_id", "")
    if not token or not chat_id or "YOUR_" in token or "YOUR_" in chat_id:
        return False
    import requests

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(url, data={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=15)
        return resp.ok
    except Exception:
        return False


# ── Rate-limited alerts ──────────────────────────────────────────────────────

def _load_alert_state():
    """Load alert state from JSON. Returns empty dict on any error."""
    try:
        with open(ALERT_STATE_PATH, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_alert_state(state):
    """Atomically save alert state (write temp file, then rename)."""
    try:
        fd, tmp_path = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(state, f)
        os.replace(tmp_path, ALERT_STATE_PATH)
    except OSError:
        pass


def rate_limited_alert(category, message, cooldown_seconds=300):
    """Send a Telegram alert only if cooldown has elapsed for this category.

    Args:
        category: e.g. "outage_start", "rogue_aa:bb:cc:dd:ee:ff"
        message: HTML-formatted message
        cooldown_seconds: Min seconds between alerts. 0 = always send.

    Returns True if the alert was sent.
    """
    state = _load_alert_state()
    last_sent = state.get(category, 0)
    now = time.time()
    if cooldown_seconds > 0 and (now - last_sent) < cooldown_seconds:
        return False
    sent = send_telegram(message)
    if sent:
        state[category] = now
        _save_alert_state(state)
    return sent
