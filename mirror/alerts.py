"""Problem alerts → a private Telegram chat, so the operator can react without
watching the admin page. Wholly optional: unset TELEGRAM_BOT_TOKEN /
TELEGRAM_CHAT_ID = disabled. Fail-open and non-blocking — an alert send runs in
a background thread and a Telegram outage must never touch a user's read.

A per-key cooldown stops an error storm from flooding the chat: repeats inside
the window are counted and summarised on the next send of that key.
"""

import json, threading, time, urllib.request

from . import provider
from .config import settings

_COOLDOWN_S = 300
_LOCK = threading.Lock()
_last = {}          # key -> {"t": monotonic of last send, "muted": suppressed count}


def enabled() -> bool:
    return bool(settings.telegram_token and settings.telegram_chat)


def send(text: str, key: str = None):
    """Fire an alert. `key` buckets similar alerts for the cooldown; None = always."""
    if not enabled():
        return
    if key:
        with _LOCK:
            s = _last.setdefault(key, {"t": 0.0, "muted": 0})
            now = time.monotonic()
            if now - s["t"] < _COOLDOWN_S:
                s["muted"] += 1
                return
            if s["muted"]:
                text += f"\n(+{s['muted']} similar in the last {_COOLDOWN_S // 60} min)"
            s["t"], s["muted"] = now, 0
    threading.Thread(target=_post, args=(text,), daemon=True).start()


def activity(text: str, key: str = None):
    """Product-activity notice — someone is actually using it (visit, upload,
    finished read). Separate from problem alerts so launch-watching can be switched
    off later (TELEGRAM_ACTIVITY=0) without touching alerting. Same no-PII rule as
    events.py: what happened, never who.

    No `key` = ping every time (uploads/reads — each one is the point, and the rate
    moat bounds them). A `key` adds the cooldown: visits and beacons are unbounded
    by anything, so a burst collapses into '+N similar' instead of a Telegram flood
    (the event log still counts every one)."""
    if enabled() and settings.telegram_activity:
        if key:
            send(text, key=key)
        else:
            threading.Thread(target=_post, args=(text,), daemon=True).start()


def _post(text: str):
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{settings.telegram_token}/sendMessage",
            data=json.dumps({"chat_id": settings.telegram_chat, "text": text}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15, context=provider._SSL_CTX) as r:
            r.read()
    except Exception as e:
        print(f"[alerts] telegram send failed: {e}", flush=True)
