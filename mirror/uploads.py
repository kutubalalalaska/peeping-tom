"""Resumable, chunked upload.

The browser slices the export into small parts and uploads each as its OWN small
request; we append them to `upload.zip` on disk, then unzip + run the normal LOCAL
pipeline. This keeps the proxy's per-request cap small, survives a dropped
connection (resume from the current byte offset), and never holds the file in RAM.

It converges with the single-shot `/api/upload` (server.py) at exactly one point:
both assemble an identical `upload.zip` on disk, then hand off to `ingest.unzip` →
the pipeline. Nothing downstream knows or cares which path built the file.

Flow:
    POST /api/upload/init         {source,lang,size,name} -> {job_id, chunk_size, max_mb}
    GET  /api/upload/{id}/offset  -> {received, size}          (resume point)
    POST /api/upload/{id}/part?offset=N   raw chunk bytes  -> {received}
    POST /api/upload/{id}/complete        -> {job_id}          (then poll /api/jobs)
"""

from fastapi import APIRouter, Request, Form, HTTPException, BackgroundTasks

from . import ingest, jobs
from .config import MODES, settings

router = APIRouter()

# server.py registers its background pipeline entrypoint here at import time — set
# via a callback instead of importing _preprocess (which would be a circular import).
PREPROCESS = None


def _client_ip(request: Request) -> str:
    """Real client IP behind Cloudflare/Caddy (mirrors server._client_ip; duplicated
    to keep this module import-independent of server.py)."""
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else ""


def _max_bytes() -> int:
    return settings.max_upload_mb * 1024 * 1024


def _received(jid: str) -> int:
    p = jobs.path(jid, "upload.zip")
    return p.stat().st_size if p.exists() else 0


@router.post("/api/upload/init")
async def upload_init(request: Request, source: str = Form("whatsapp"),
                      lang: str = Form("en"), size: int = Form(0), name: str = Form(""),
                      mode: str = Form("fast"), slice_range: str = Form("")):
    """Begin a resumable upload: apply the hosted rate cap + a total-size guard up
    front (an over-cap file is rejected before a single byte is sent), create the job,
    and return its id + the chunk size to use."""
    if size and size > _max_bytes():
        raise HTTPException(413, f"That chat export is too large (max {settings.max_upload_mb} MB).")
    if settings.hosted:
        sid, ip = request.state.sid, _client_ip(request)
        if jobs.recent_count(settings.rate_window_seconds, sid=sid) >= settings.rate_max_per_session:
            raise HTTPException(429, "You've reached your reads for now. Try again later.")
        if ip and jobs.recent_count(settings.rate_window_seconds, ip=ip) >= settings.rate_max_per_ip:
            raise HTTPException(429, "This network has reached its reads for now. Try again later.")
        jid = jobs.create(source=source, sid=sid, ip=ip)
    else:
        jid = jobs.create(source=source)
    jobs.set_status(jid, state="uploading", message="receiving your chat…",
                    lang=(lang or "en").split("-")[0].lower()[:5],
                    mode=mode if mode in MODES else "fast",
                    slice_range=(slice_range.strip()[:64] or None),
                    upload_size=int(size or 0), upload_name=str(name or "")[:200])
    return {"job_id": jid, "chunk_size": 8 * 1024 * 1024, "max_mb": settings.max_upload_mb}


@router.get("/api/upload/{jid}/offset")
def upload_offset(jid: str):
    """Bytes already on disk for this upload — the client resumes by sending the rest
    starting at `received` (survives a dropped connection or a page reload)."""
    if not jobs.exists(jid):
        raise HTTPException(404, "no such upload")
    s = jobs.get_status(jid) or {}
    return {"received": _received(jid), "size": int(s.get("upload_size") or 0)}


@router.post("/api/upload/{jid}/part")
async def upload_part(jid: str, request: Request, offset: int = 0):
    """Append ONE chunk at byte `offset`, which MUST equal what we already hold (parts
    go up sequentially; on a mismatch the client re-syncs via /offset — this prevents
    gaps and overlaps). Streamed to disk, so memory stays bounded regardless of size."""
    if not jobs.exists(jid):
        raise HTTPException(404, "no such upload")
    cur = _received(jid)
    if offset != cur:
        raise HTTPException(409, detail={"error": "offset mismatch", "received": cur})
    limit = _max_bytes()
    clen = int(request.headers.get("content-length") or 0)
    if clen and cur + clen > limit:
        raise HTTPException(413, f"That chat export is too large (max {settings.max_upload_mb} MB).")
    p = jobs.path(jid, "upload.zip")
    written = cur
    with p.open("ab") as out:
        async for chunk in request.stream():
            if not chunk:
                continue
            written += len(chunk)
            if written > limit:                          # backstop if content-length lied / was absent
                out.truncate(cur)                        # roll this part back; keep only valid bytes
                jobs.set_status(jid, state="error",
                                message=f"That chat export is too large (max {settings.max_upload_mb} MB).")
                raise HTTPException(413, f"That chat export is too large (max {settings.max_upload_mb} MB).")
            out.write(chunk)
    return {"received": _received(jid)}


@router.post("/api/upload/{jid}/complete")
async def upload_complete(jid: str, bg: BackgroundTasks):
    """All parts received: unzip and hand off to the normal LOCAL pipeline (decode →
    transcript → read) — the identical handoff the single-shot path uses."""
    if not jobs.exists(jid):
        raise HTTPException(404, "no such upload")
    p = jobs.path(jid, "upload.zip")
    if not p.exists() or p.stat().st_size == 0:
        raise HTTPException(400, "no upload data was received")
    s = jobs.get_status(jid) or {}
    declared = int(s.get("upload_size") or 0)
    if declared and _received(jid) != declared:
        raise HTTPException(409, detail={"error": "incomplete upload",
                                         "received": _received(jid), "size": declared})
    try:
        ingest.unzip(p, jobs.path(jid, "export"))
    except Exception as e:
        jobs.set_status(jid, state="error", message=f"couldn't open the upload: {e}")
        raise HTTPException(400, "the upload wasn't a valid zip")
    p.unlink(missing_ok=True)
    if PREPROCESS is None:                               # should never happen once server wired it
        jobs.set_status(jid, state="error", message="server not ready")
        raise HTTPException(500, "pipeline not wired")
    bg.add_task(PREPROCESS, jid)
    return {"job_id": jid}
