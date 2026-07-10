"""FastAPI server — the THIN web layer. All pipeline logic lives in pipeline.py.

API:
    GET    /api/config                  {hosted, frontier_ready, routes[], default_route, read_ttl_seconds}
    GET    /api/quota                   reads left for this cookie-session (hosted tier)
    POST   /api/upload                  zip + source + mode -> job -> pipeline.run (background)
    GET    /api/jobs/{id}               status (poll): state, phase, progress, media_requests…
    GET    /api/jobs/{id}/transcript    the exact text that crossed the boundary
    GET    /api/jobs/{id}/result        the read JSON (+ validated citations)
    GET    /api/jobs/{id}/retained      what we currently hold (transparency panel)
    DELETE /api/jobs/{id}               delete everything for this job (returns a receipt)
    GET    /api/jobs/{id}/media/{f}     serve a raw media file LOCALLY (glimpses/receipts)
    GET    /api/jobs/{id}/messages      resolve cited ids -> messages (clickable receipts)

Frontend: serves the built React SPA from WEB_DIR if present, else placeholder pages.
"""

import json, threading, time, uuid
from pathlib import Path

from fastapi import FastAPI, UploadFile, Form, BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from . import decode, ingest, jobs, mediatypes, pipeline, provider, uploads
from .config import MODES, settings

HERE = Path(__file__).parent
WEB = Path(settings.web_dir) if settings.web_dir else None
SPA = bool(WEB and (WEB / "index.html").exists())

app = FastAPI(title="Drop 001: Peeping Tom")

# Resumable chunked-upload routes (mirror/uploads.py). Registered here, BEFORE the
# SPA catch-all below, so GET /api/upload/{id}/offset isn't shadowed by it.
app.include_router(uploads.router)


def _client_ip(request: Request) -> str:
    """Real client IP, honouring a CDN / reverse proxy in front (Cloudflare, Caddy).
    Falls back to the socket peer for a direct connection."""
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else ""


@app.middleware("http")
async def _session_cookie(request: Request, call_next):
    """Give every browser an opaque, no-PII session id in an httponly cookie. It only
    powers the rate moat + 'reads left' readout — never tied to identity. Incognito /
    cleared cookies simply get a fresh one (a soft moat; the IP cap is the backstop)."""
    sid = request.cookies.get(settings.session_cookie)
    fresh = not sid
    if fresh:
        sid = uuid.uuid4().hex
    request.state.sid = sid
    response = await call_next(request)
    if fresh:
        response.set_cookie(settings.session_cookie, sid, max_age=60 * 60 * 24 * 30,
                            httponly=True, samesite="lax", secure=settings.cookie_secure)
    return response


@app.on_event("startup")
def _start_sweeper():
    """In-process self-destruct sweeper: purge expired reads + abandoned jobs on a
    timer (works out of the box; scripts/purge.py covers a real system cron too)."""
    def loop():
        while True:
            try:
                n = jobs.purge_expired()
                if n:
                    print(f"[purge] deleted {n} expired/abandoned job(s)", flush=True)
            except Exception as e:
                print(f"[purge] error: {e}", flush=True)
            time.sleep(max(15, settings.purge_interval_seconds))
    threading.Thread(target=loop, daemon=True).start()


@app.on_event("startup")
def _warm_vision():
    """Preload the decode VLM at boot (background) so the first image isn't a cold
    load. Non-blocking + fail-open (decode.warm_up swallows errors)."""
    threading.Thread(target=decode.warm_up, daemon=True).start()


# Boot-time auth cache: probe each read route's key at startup so a wrong/missing key
# fails LOUDLY in the logs here, not silently at the user's first read (Route.ready()
# only checks that a base_url+model exist, never the key). Surfaced in /api/config.
_ROUTE_AUTH: dict = {}


@app.on_event("startup")
def _check_route_auth():
    for r in settings.routes:
        try:
            ok, detail = provider.probe_auth(r)
        except Exception as e:                          # never let a probe block startup
            ok, detail = None, f"probe crashed: {e}"
        _ROUTE_AUTH[r.id] = {"ok": ok, "detail": detail}
        tag = {True: "OK", False: "FAILED", None: "unverified"}[ok]
        msg = f"[route {r.id}] auth {tag}: {detail} (model={r.model or '-'})"
        print(("!!! " + msg + " — reads on this route WILL fail until fixed") if ok is False
              else msg, flush=True)


# ---- API ----
@app.get("/api/config")
def get_config():
    # Merge in the boot-time auth probe so a bad key is visible here, not just at read time.
    routes = []
    for r in settings.public_routes():
        a = _ROUTE_AUTH.get(r["id"])
        routes.append({**r, "auth_ok": a["ok"], "auth_detail": a["detail"]} if a else r)
    return {"hosted": settings.hosted, "frontier_ready": settings.frontier_ready(),
            "routes": routes, "default_route": settings.default_route_id(),
            "read_ttl_seconds": settings.read_ttl_seconds,
            "max_upload_mb": settings.max_upload_mb}


@app.get("/api/quota")
def quota(request: Request):
    """Reads left for this cookie-session (Landing readout). Hosted tier only; off-tier
    there is no cap, so limit is null. No PII — keyed on the opaque session cookie."""
    if not settings.hosted:
        return {"enabled": False, "limit": None, "used": 0, "remaining": None}
    used = jobs.recent_count(settings.rate_window_seconds, sid=request.state.sid)
    limit = settings.rate_max_per_session
    return {"enabled": True, "limit": limit, "used": used,
            "remaining": max(0, limit - used), "window_seconds": settings.rate_window_seconds}


@app.post("/api/upload")
async def upload(request: Request, bg: BackgroundTasks, file: UploadFile,
                 source: str = Form("whatsapp"), lang: str = Form("en"),
                 mode: str = Form("fast"), slice_range: str = Form("")):
    # Abuse moat (hosted tier only): cap reads per cookie-session and per IP over a
    # rolling window. No login, no PII — just enough to stop scraping + runaway spend.
    if settings.hosted:
        sid, ip = request.state.sid, _client_ip(request)
        if jobs.recent_count(settings.rate_window_seconds, sid=sid) >= settings.rate_max_per_session:
            raise HTTPException(429, "You've reached your reads for now. Try again later.")
        if ip and jobs.recent_count(settings.rate_window_seconds, ip=ip) >= settings.rate_max_per_ip:
            raise HTTPException(429, "This network has reached its reads for now. Try again later.")
        jid = jobs.create(source=source, sid=sid, ip=ip)
    else:
        jid = jobs.create(source=source)
    # The chosen UI language sets the read's OUTPUT language (whitelisted in
    # protocol.lang_directive). `mode` picks the read pipeline (fast | deep).
    # `slice_range` is honest provenance from the client-side slicer: the date
    # window the user cut a too-big export down to (surfaced on the result).
    jobs.set_status(jid, lang=(lang or "en").split("-")[0].lower()[:5],
                    mode=mode if mode in MODES else "fast",
                    slice_range=(slice_range.strip()[:64] or None))
    zp = jobs.path(jid, "upload.zip")
    # Stream the upload to disk in chunks — a multi-GB export would otherwise load
    # whole into RAM via file.read() and risk OOM on a small Docker VM.
    with zp.open("wb") as out:
        while chunk := await file.read(4 * 1024 * 1024):
            out.write(chunk)
    ingest.unzip(zp, jobs.path(jid, "export"))
    zp.unlink(missing_ok=True)
    bg.add_task(pipeline.run, jid)
    return {"job_id": jid}


@app.get("/api/jobs/{job_id}")
def status(job_id: str):
    s = jobs.get_status(job_id)
    if not s:
        raise HTTPException(404)
    return s


@app.get("/api/jobs/{job_id}/transcript", response_class=HTMLResponse)
def get_transcript(job_id: str):
    p = jobs.path(job_id, "transcript.txt")
    if not p.exists():
        raise HTTPException(404, "not available")
    return f"<pre>{p.read_text()}</pre>"


@app.get("/api/jobs/{job_id}/result")
def result(job_id: str):
    p = jobs.path(job_id, "read.json")
    if not p.exists():
        raise HTTPException(404, "not ready")
    return JSONResponse(json.loads(p.read_text()))


@app.get("/api/jobs/{job_id}/retained")
def retained(job_id: str):
    if not jobs.exists(job_id):
        raise HTTPException(404)
    return jobs.retained(job_id)


@app.delete("/api/jobs/{job_id}")
def delete(job_id: str):
    if not jobs.exists(job_id):
        raise HTTPException(404)
    jobs.delete(job_id)
    return {"deleted": True, "at": time.strftime("%H:%M:%S"), "retained": {"everything": "nothing"}}


@app.get("/api/jobs/{job_id}/media/{name}")
def media(job_id: str, name: str):
    hits = list(jobs.path(job_id, "export").rglob(name))
    if not hits:
        raise HTTPException(404)
    return FileResponse(hits[0])


@app.get("/api/jobs/{job_id}/messages")
def messages(job_id: str, ids: str = ""):
    """Resolve cited message ids -> {id, ts, sender, text, media:[{file,type,caption}]}
    so the read's [#id] citations become clickable receipts. `ids=1,2,3` selects
    just the cited messages (the read's `citations`); omitted returns all."""
    mp = jobs.path(job_id, "messages.json")
    if not mp.exists():
        raise HTTPException(404, "messages not available")
    want = {int(x) for x in ids.split(",") if x.strip().isdigit()} if ids else None
    medp = jobs.path(job_id, "media.json")
    media_idx = json.loads(medp.read_text()) if medp.exists() else {}
    out = []
    for m in json.loads(mp.read_text()):
        if want is not None and m["id"] not in want:
            continue
        rich = []
        for f in m.get("media", []):
            rec = media_idx.get(f) or {}
            rich.append({"file": f, "type": rec.get("type") or mediatypes.kind(f),
                         "caption": decode._caption_of(rec) if rec else None})
        out.append({"id": m["id"], "ts": m["ts"], "sender": m["sender"],
                    "text": m["text"], "media": rich})
    return out


# The chunked-upload router hands finished uploads back to the LOCAL pipeline via
# this callback (set here to avoid a circular import between server and uploads).
uploads.PREPROCESS = pipeline.run


# ---- frontend ----
if SPA:
    app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")

    @app.get("/{full_path:path}", response_class=HTMLResponse)
    def spa(full_path: str):
        # serve real files if present, else fall back to index.html (client-side routing)
        f = WEB / full_path
        if full_path and f.is_file():
            return FileResponse(f)
        return (WEB / "index.html").read_text()
else:
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.get("/", response_class=HTMLResponse)
    def home():
        return (HERE / "static" / "upload.html").read_text()

    @app.get("/result/{job_id}", response_class=HTMLResponse)
    def result_page(job_id: str):
        return (HERE / "static" / "result.html").read_text()
