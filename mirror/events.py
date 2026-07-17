"""Ops event log — append-only JSONL of job-level TECHNICAL facts under
DATA_DIR/ops/. Powers the /admin page and the Telegram problem alerts.

What lands here: upload accepted/refused (+reason), read started/done/failed,
durations, sizes, citation stats, 5xx errors. What NEVER lands here: IPs,
session ids, participant names, message text, captions. That's deliberate —
this log OUTLIVES the jobs' TTL purge precisely because it holds nothing
private (the purge deletes people's data; this is the machine's diary).
"""

import json, threading, time
from pathlib import Path

from .config import settings

FILE = Path(settings.data_dir) / "ops" / "events.jsonl"
_ROTATE_BYTES = 5 * 1024 * 1024        # decades at friends-test volume; one .1 generation kept
_LOCK = threading.Lock()


def log(event: str, job: str = None, **fields):
    """Append one event. Never raises — ops logging must not break the pipeline."""
    rec = {"ts": round(time.time(), 3), "event": event}
    if job:
        rec["job"] = job
    rec.update({k: v for k, v in fields.items() if v is not None})
    try:
        with _LOCK:
            FILE.parent.mkdir(parents=True, exist_ok=True)
            if FILE.exists() and FILE.stat().st_size > _ROTATE_BYTES:
                FILE.replace(FILE.with_suffix(".jsonl.1"))
            with FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[events] write failed: {e}", flush=True)
    return rec


def read(since: float = None, limit: int = None) -> list:
    """Events (oldest first), optionally only those at/after `since` (epoch) and/or
    the last `limit`. Reads the rotated generation too, so a fresh rotation never
    blanks the admin page's 7-day counters."""
    items = []
    for p in (FILE.with_suffix(".jsonl.1"), FILE):
        try:
            with p.open("r", encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue                    # partial trailing line mid-write
                    if since is None or rec.get("ts", 0) >= since:
                        items.append(rec)
        except FileNotFoundError:
            continue
        except Exception as e:
            print(f"[events] read failed: {e}", flush=True)
    return items[-limit:] if limit else items
