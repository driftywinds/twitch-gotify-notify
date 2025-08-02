#!/usr/bin/env python3
"""
Monitor specified Twitch channels and send Gotify notifications when they go
online or offline.

Configuration is read from a .env file in the same directory:
- TWITCH_CLIENT_ID=...
- TWITCH_CLIENT_SECRET=...
- CHANNELS=comma,separated,list
- GOTIFY_URL=https://gotify.example.com
- GOTIFY_TOKEN=your_gotify_token
- POLL_SECONDS=60 (optional)
"""

import os
import time
import logging
from typing import Dict, List

import requests
from dotenv import load_dotenv

# Load .env file
load_dotenv()

CONFIG = {
    "TWITCH_CLIENT_ID": os.getenv("TWITCH_CLIENT_ID"),
    "TWITCH_CLIENT_SECRET": os.getenv("TWITCH_CLIENT_SECRET"),
    "CHANNELS": [c.strip().lower() for c in os.getenv("CHANNELS", "").split(",") if c.strip()],
    "GOTIFY_URL": os.getenv("GOTIFY_URL"),
    "GOTIFY_TOKEN": os.getenv("GOTIFY_TOKEN"),
    "POLL_SECONDS": int(os.getenv("POLL_SECONDS", "60")),
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

TWITCH_TOKEN = None
TWITCH_TOKEN_EXPIRES = 0


def get_twitch_token() -> str:
    """Get (and cache) an app access token for the Twitch Helix API."""
    global TWITCH_TOKEN, TWITCH_TOKEN_EXPIRES
    if TWITCH_TOKEN and time.time() < TWITCH_TOKEN_EXPIRES - 60:
        return TWITCH_TOKEN

    url = "https://id.twitch.tv/oauth2/token"
    params = {
        "client_id": CONFIG["TWITCH_CLIENT_ID"],
        "client_secret": CONFIG["TWITCH_CLIENT_SECRET"],
        "grant_type": "client_credentials",
    }
    r = requests.post(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    TWITCH_TOKEN = data["access_token"]
    TWITCH_TOKEN_EXPIRES = time.time() + data.get("expires_in", 3600)
    logging.info("Obtained new Twitch access token (valid %s s)", data.get("expires_in"))
    return TWITCH_TOKEN


def get_stream_status(channels: List[str]) -> Dict[str, bool]:
    """Return mapping of channel -> is_online by querying Helix /streams."""
    token = get_twitch_token()
    headers = {
        "Client-ID": CONFIG["TWITCH_CLIENT_ID"],
        "Authorization": f"Bearer {token}",
    }
    online = {}
    for i in range(0, len(channels), 100):  # Helix allows up to 100 logins per call
        batch = channels[i:i + 100]
        params = [("user_login", c) for c in batch]
        url = "https://api.twitch.tv/helix/streams"
        r = requests.get(url, headers=headers, params=params, timeout=10)
        if r.status_code == 401:  # token expired early – refresh once
            token = get_twitch_token()
            headers["Authorization"] = f"Bearer {token}"
            r = requests.get(url, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        online_names = {s["user_login"].lower() for s in data}
        for name in batch:
            online[name] = name in online_names
    return online


def send_gotify(title: str, message: str, priority: int = 5) -> None:
    """Send a notification to Gotify."""
    url = f"{CONFIG['GOTIFY_URL'].rstrip('/')}/message?token={CONFIG['GOTIFY_TOKEN']}"
    payload = {"title": title, "message": message, "priority": priority}
    r = requests.post(url, json=payload, timeout=10)
    if r.status_code != 200:
        logging.error("Gotify send failed %s – %s", r.status_code, r.text)
    else:
        logging.info("Sent Gotify notification: %s", title)


def main() -> None:
    channels = CONFIG["CHANNELS"]
    if not channels:
        logging.error("No channels configured – set CHANNELS in your .env file.")
        return

    # 🔔 Send test notification
    logging.info("Sending test Gotify notification...")
    send_gotify("✅ Twitch Monitor Started", "This is a test notification to confirm Gotify is working.", priority=1)

    # 🔍 Check current stream status on startup and notify if any are live
    logging.info("Checking initial stream status...")
    try:
        current = get_stream_status(channels)
        for name, is_online in current.items():
            if is_online:
                send_gotify(f"{name} is already LIVE 🎥", f"{name} was already online at startup.", priority=4)
            else:
                logging.info(f"{name} is currently offline at startup.")
    except Exception:
        logging.exception("Error checking initial stream status")

    last_status: Dict[str, bool] = {c: None for c in channels}
    logging.info("Monitoring %d channel(s): %s", len(channels), ", ".join(channels))

    while True:
        try:
            current = get_stream_status(channels)
            for name, online in current.items():
                previous = last_status.get(name)
                if previous is None:
                    last_status[name] = online  # first run baseline
                    continue
                if online and not previous:
                    send_gotify(f"{name} is LIVE 🎥", f"{name} just went online.", priority=5)
                elif not online and previous:
                    send_gotify(f"{name} is offline 💤", f"{name} just ended the stream.", priority=3)
                last_status[name] = online
        except Exception:
            logging.exception("Error during poll loop")
        time.sleep(CONFIG["POLL_SECONDS"])


if __name__ == "__main__":
    main()
