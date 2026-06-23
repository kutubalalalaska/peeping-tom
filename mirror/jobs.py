"""On-disk job store under DATA_DIR. Everything lives in jobs/<id>/ — nothing
hidden in memory, so the whole lifecycle is inspectable.

    jobs/<id>/
        export/             unzipped chat + raw media (LOCAL ONLY)
        work/               decode scratch (frames, png conversions)
        media.json          decoded captions / transcripts / cast
        transcript.txt      the text that crosses the boundary
        read.json           the frontier result
        status.json         {state, message, source, me, participants, progress, recent, ...}
"""

import json, os, shutil, threading, time, uuid
from pathlib import Path

from .config import settings

ROOT = Path(settings.data_dir) / "jobs"

# Status is a single JSON file updated from two places at once — the background
# decode thread (progress/recent) and API requests (role pick). Serialize the
# read-modify-write so neither clobbers the other's fields.
_LOCK = threading.Lock()


def _d(job_id: str) -> Path:
    return ROOT / job_id


def create(source: str) -> str:
    jid = uuid.uuid4().hex[:12]
    (_d(jid) / "export").mkdir(parents=True, exist_ok=True)
    (_d(jid) / "work").mkdir(parents=True, exist_ok=True)
    set_status(jid, state="uploaded", message="upload received", source=source)
    return jid


def exists(job_id: str) -> bool:
    return _d(job_id).exists()


def path(job_id: str, name: str) -> Path:
    return _d(job_id) / name


def set_status(job_id: str, **fields):
    d = _d(job_id); d.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        cur = get_status(job_id) or {}
        cur.update(fields); cur["ts"] = time.time()
        # Write atomically: the status poller reads status.json WITHOUT the lock,
        # and streamed reads update it many times a second — a temp-file + rename
        # means a reader never catches a half-written file.
        tmp = d / "status.json.tmp"
        tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2))
        os.replace(tmp, d / "status.json")
        return cur


def get_status(job_id: str):
    f = _d(job_id) / "status.json"
    return json.loads(f.read_text()) if f.exists() else None


def retained(job_id: str) -> dict:
    """What we currently hold for this job — powers the transparency panel."""
    d = _d(job_id)
    return {
        "raw_media": (d / "export").exists(),
        "transcript": (d / "transcript.txt").exists(),
        "read": (d / "read.json").exists(),
    }


def delete_raw(job_id: str):
    """Drop the most sensitive artifacts: raw media + the assembled transcript,
    including messages.json (the transcript in structured form that powers
    receipts). Receipts therefore only work on the retained, non-ephemeral path."""
    shutil.rmtree(_d(job_id) / "export", ignore_errors=True)
    shutil.rmtree(_d(job_id) / "work", ignore_errors=True)
    path(job_id, "transcript.txt").unlink(missing_ok=True)
    path(job_id, "messages.json").unlink(missing_ok=True)


def delete(job_id: str):
    """Remove everything for this job."""
    shutil.rmtree(_d(job_id), ignore_errors=True)
