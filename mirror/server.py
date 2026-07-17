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

from . import admin, alerts, decode, events, ingest, jobs, mediatypes, pipeline, provider, uploads
from .config import MODES, settings

HERE = Path(__file__).parent
WEB = Path(settings.web_dir) if settings.web_dir else None
SPA = bool(WEB and (WEB / "index.html").exists())

app = FastAPI(title="Drop 001: Peeping Tom")

# Resumable chunked-upload routes (mirror/uploads.py). Registered here, BEFORE the
# SPA catch-all below, so GET /api/upload/{id}/offset isn't shadowed by it.
app.include_router(uploads.router)
app.include_router(admin.router)                    # /admin + /api/admin/* (404 unless ADMIN_PASS set)


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


@app.middleware("http")
async def _ops_watch(request: Request, call_next):
    """Ops visibility: any 5xx — returned or raised — lands in the event log and
    pings the operator. 4xx don't (rate caps and purged-job 404s are normal life),
    and neither does 503 (the deliberate out-of-credits refusal, logged with its
    reason at the source)."""
    try:
        response = await call_next(request)
    except Exception as e:
        events.log("http_5xx", path=request.url.path, error=str(e)[:200])
        alerts.send(f"💥 crash on {request.url.path}: {str(e)[:200]}", key="http_5xx")
        raise
    if response.status_code >= 500 and response.status_code != 503:
        events.log("http_5xx", path=request.url.path, status=response.status_code)
        alerts.send(f"💥 HTTP {response.status_code} on {request.url.path}", key="http_5xx")
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
    threading.Thread(target=_refresh_credits, daemon=True).start()


# Landing honesty: when the OpenRouter account can't fund a read anymore, say so
# instead of letting uploads march into a mid-read 402. Refreshed lazily in the
# background (config must never block on a remote probe); fail-open when unknown.
_CREDITS = {"ts": 0.0, "remaining": None}
_CREDITS_TTL = 300
_CREDITS_FLOOR = 1.0        # USD — below this, a typical read risks dying mid-flight


_CREDITS_WARN = 5.0         # heads-up band: top up before the gate closes


def _refresh_credits():
    r = settings.route()
    was = _CREDITS["remaining"]
    rem = provider.probe_credits(r) if r else None
    _CREDITS["remaining"] = rem
    _CREDITS["ts"] = time.time()
    # Operator alert on band TRANSITIONS only (ok → low → gated and back), so a
    # steady balance never repeats itself into the Telegram chat.
    band = lambda v: None if v is None else (
        "gated" if v < _CREDITS_FLOOR else "low" if v < _CREDITS_WARN else "ok")
    b0, b1 = band(was), band(rem)
    if b1 == b0 or b1 is None:
        return
    if b1 == "gated":
        events.log("out_of_credits", remaining=round(rem, 2))
        alerts.send(f"💸 OpenRouter balance ${rem:.2f} — below the ${_CREDITS_FLOOR:.0f} floor, uploads are now GATED")
    elif b1 == "low" and b0 != "gated":
        alerts.send(f"💸 OpenRouter balance ${rem:.2f} — running low, uploads gate below ${_CREDITS_FLOOR:.0f}")
    elif b0 == "gated":
        events.log("credits_restored", remaining=round(rem, 2))
        alerts.send(f"✅ OpenRouter balance ${rem:.2f} — uploads open again")


def _out_of_credits() -> bool:
    if time.time() - _CREDITS["ts"] > _CREDITS_TTL:
        _CREDITS["ts"] = time.time()                    # claim the slot — no probe stampede
        threading.Thread(target=_refresh_credits, daemon=True).start()
    rem = _CREDITS["remaining"]
    return rem is not None and rem < _CREDITS_FLOOR


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
            "max_upload_mb": settings.max_upload_mb,
            "out_of_credits": _out_of_credits()}


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
                 mode: str = Form("fast"), slice_range: str = Form(""),
                 slice_before: int = Form(0), slice_after: int = Form(0),
                 slice_full: str = Form("")):
    # No new uploads once the hosted account can't fund the read — the landing's
    # notice made this visible; here it's enforced (transparency = code, not copy).
    if _out_of_credits():
        events.log("upload_refused", reason="out_of_credits", via="single")
        raise HTTPException(503, "We are out of free trials for now. Try later, or run the app yourself.")
    # Abuse moat (hosted tier only): cap reads per cookie-session and per IP over a
    # rolling window. No login, no PII — just enough to stop scraping + runaway spend.
    if settings.hosted:
        sid, ip = request.state.sid, _client_ip(request)
        if jobs.recent_count(settings.rate_window_seconds, sid=sid) >= settings.rate_max_per_session:
            events.log("upload_refused", reason="rate_session", via="single")
            raise HTTPException(429, "You've reached your reads for now. Try again later.")
        if ip and jobs.recent_count(settings.rate_window_seconds, ip=ip) >= settings.rate_max_per_ip:
            events.log("upload_refused", reason="rate_ip", via="single")
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
                    slice_range=(slice_range.strip()[:64] or None),
                    slice_before=max(0, int(slice_before or 0)),
                    slice_after=max(0, int(slice_after or 0)),
                    slice_full=(slice_full.strip()[:64] or None))
    zp = jobs.path(jid, "upload.zip")
    # Stream the upload to disk in chunks — a multi-GB export would otherwise load
    # whole into RAM via file.read() and risk OOM on a small Docker VM.
    with zp.open("wb") as out:
        while chunk := await file.read(4 * 1024 * 1024):
            out.write(chunk)
    size_mb = round(zp.stat().st_size / 1048576, 1)
    ingest.unzip(zp, jobs.path(jid, "export"))
    zp.unlink(missing_ok=True)
    events.log("upload_accepted", job=jid, via="single", source=source,
               mode=mode if mode in MODES else "fast", size_mb=size_mb)
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
uploads.GATE = _out_of_credits

# The admin page reads the live credits/auth state through the same callback
# pattern (admin.py must not import server.py).
admin.CREDITS = _CREDITS
admin.ROUTE_AUTH = _ROUTE_AUTH
admin.OUT_OF_CREDITS = _out_of_credits


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
