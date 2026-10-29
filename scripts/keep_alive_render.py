#!/usr/bin/env python3
"""
Render 24/7 Zero-Sleep Keep-Alive & Auto-Healer for ModedSMS OTP Bot.
Pings the Render web service URL every few minutes to prevent inactivity spin-down.
If down or suspended, automatically uses the Render REST API to wake up or restart it.
"""

import os
import sys
import json
import time
import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests library not found. Please install requests.")
    sys.exit(1)

BASE_DIR = Path(__file__).parent.parent.resolve()
CONFIG_FILE = BASE_DIR / "render_config.json"
LOG_FILE = BASE_DIR / "keep_alive_render.log"


def log(msg):
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{now_str}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_config():
    cfg = {}
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            log(f"Error loading {CONFIG_FILE}: {e}")

    api_key = os.environ.get("RENDER_API_KEY") or cfg.get("api_key") or "rnd_iPoVaPGzJuiJwmEM23cpVEwx0BG5"
    owner_id = os.environ.get("RENDER_OWNER_ID") or cfg.get("owner_id")
    service_id = os.environ.get("RENDER_SERVICE_ID") or cfg.get("service_id") or "srv-dbfg24lg1s2s73fmap80"
    service_url = os.environ.get("RENDER_SERVICE_URL") or cfg.get("service_url") or "https://modedsms-otp-bot.onrender.com"
    service_name = os.environ.get("RENDER_SERVICE_NAME") or cfg.get("service_name") or "modedsms-otp-bot"

    return {
        "api_key": api_key,
        "owner_id": owner_id,
        "service_id": service_id,
        "service_url": service_url,
        "service_name": service_name,
    }


def restart_service(api_key, service_id):
    """Triggers restart via Render REST API."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json"
    }
    url = f"https://api.render.com/v1/services/{service_id}/restart"
    try:
        r = requests.post(url, headers=headers, timeout=20)
        if r.status_code in (200, 202):
            log(f"✅ Restart triggered successfully via Render API: HTTP {r.status_code}")
            return True
        else:
            log(f"⚠️ Failed to restart via Render API: {r.status_code} - {r.text[:150]}")
    except Exception as e:
        log(f"⚠️ Exception calling Render restart API: {e}")
    return False


def resume_service(api_key, service_id):
    """Triggers resume via Render REST API if suspended."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json"
    }
    url = f"https://api.render.com/v1/services/{service_id}/resume"
    try:
        r = requests.post(url, headers=headers, timeout=20)
        if r.status_code in (200, 202):
            log(f"✅ Resume triggered successfully via Render API: HTTP {r.status_code}")
            return True
        else:
            log(f"⚠️ Failed to resume via Render API: {r.status_code} - {r.text[:150]}")
    except Exception as e:
        log(f"⚠️ Exception calling Render resume API: {e}")
    return False


def ping_and_heal():
    cfg = load_config()
    service_url = cfg.get("service_url")
    service_id = cfg.get("service_id")
    api_key = cfg.get("api_key")

    if not service_url:
        log("No service_url configured.")
        return

    log(f"Pinging service health check at {service_url} ...")
    try:
        resp = requests.get(service_url, timeout=25)
        status = resp.status_code
        text = resp.text[:100]

        if "suspended" in text.lower():
            log(f"🚨 Service suspended! Attempting auto-resume...")
            if api_key and service_id:
                resume_service(api_key, service_id)
            return

        if status == 200:
            log(f"🟢 Service ALIVE and healthy! HTTP 200 - {text}")
            return
        elif status in (502, 503, 504, 500):
            log(f"🔴 Service degraded or down (HTTP {status}). Triggering restart...")
            if api_key and service_id:
                restart_service(api_key, service_id)
        else:
            log(f"🟡 Unexpected status code: HTTP {status} - {text}")

    except requests.exceptions.Timeout:
        log("⏰ Timeout contacting service (>25s). Triggering wake-up / restart...")
        if api_key and service_id:
            restart_service(api_key, service_id)
    except Exception as e:
        log(f"❌ Connection error: {e}. Triggering recovery...")
        if api_key and service_id:
            restart_service(api_key, service_id)


if __name__ == "__main__":
    ping_and_heal()
