"""FastAPI server — the whole web layer. Uses only internal modules.

API:
    GET    /api/config                  {hosted, frontier_ready, routes[], default_route}
    POST   /api/upload                  zip + source -> job -> LOCAL preprocess (background)
    GET    /api/jobs/{id}               status (poll): state, participants, progress, recent…
    POST   /api/jobs/{id}/role          which participant is "me" (picked from the parsed list)
    GET    /api/jobs/{id}/transcript    the exact text that will cross the boundary (review)
    POST   /api/jobs/{id}/send          cross the boundary: run the read (form: route=<id>)
    GET    /api/jobs/{id}/result        the read JSON (+ resolved citations)
    GET    /api/jobs/{id}/retained      what we currently hold (transparency panel)
    DELETE /api/jobs/{id}               delete everything for this job (returns a receipt)
    GET    /api/jobs/{id}/media/{f}     serve a raw media file LOCALLY (glimpses/receipts)
    GET    /api/jobs/{id}/messages      resolve cited ids -> messages (clickable receipts)

Frontend: serves the built React SPA from WEB_DIR if present, else placeholder pages.
"""

import json, time
from pathlib import Path

from fastapi import FastAPI, UploadFile, Form, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from . import ingest, decode, transcript as T, frontier, jobs
from .config import settings

HERE = Path(__file__).parent
WEB = Path(settings.web_dir) if settings.web_dir else None
SPA = bool(WEB and (WEB / "index.html").exists())

app = FastAPI(title="Inward Mirror")

_DECODABLE = {"image", "sticker", "audio", "video"}


# ---- pipeline phases (background) ----
def _preprocess(job_id: str):
    """Parse first (instant) so the role selector + participant list exist
    immediately, then decode media (slow), streaming progress + glimpses."""
    try:
        exp = jobs.path(job_id, "export")
        chat = ingest.find_chat(exp)
        if not chat:
            jobs.set_status(job_id, state="error", message="no chat .txt found in the upload"); return
        msgs = ingest.parse(chat)
        if not msgs:
            jobs.set_status(job_id, state="error", message="couldn't read any messages from this export"); return

        media = ingest.media_files(exp)
        # Iterative discovery decodes only audio up front (text-first); images are
        # decoded on demand during the read loop. Otherwise: cheap-all decode now.
        to_decode = [f for f in media if decode.file_type(f) == "audio"] if settings.iterative_discovery else media
        total = sum(1 for f in to_decode if decode.file_type(f) in _DECODABLE)
        # Iterative discovery is text-first: the up-front pass only transcribes voice
        # notes (images are opened on demand during the read). Present it as ONE
        # "parsing" step — parse is already done; this is just audio (or instant).
        if settings.iterative_discovery:
            msg = ("parsing your chat and transcribing voice notes — on this machine…"
                   if total else "parsing your chat on this machine…")
        else:
            msg = "decoding your media on this machine…"
        jobs.set_status(job_id, state="inspecting", message=msg,
                        participants=ingest.participants(msgs), recent=[],
                        progress={"done": 0, "total": total, "pct": 0 if total else 100})

        def on_progress(done: int, total: int, item: dict):
            cur = jobs.get_status(job_id) or {}
            recent = cur.get("recent", [])
            if item and item.get("caption"):
                recent = (recent + [item])[-12:]
            pct = int(done * 100 / total) if total else 100
            jobs.set_status(job_id, progress={"done": done, "total": total, "pct": pct}, recent=recent)

        decoded = decode.decode_media(to_decode, jobs.path(job_id, "work"), on_progress)
        jobs.path(job_id, "media.json").write_text(json.dumps(decoded, ensure_ascii=False, indent=2))
        jobs.path(job_id, "transcript.txt").write_text(T.assemble(msgs, decoded))
        # structured transcript, keyed by id — powers clickable [#id] receipts
        jobs.path(job_id, "messages.json").write_text(json.dumps(
            [{"id": m.id, "ts": m.ts, "sender": m.sender, "text": m.text, "media": m.media} for m in msgs],
            ensure_ascii=False))
        jobs.set_status(job_id, state="ready", message="ready — pick yourself, then continue",
                        progress={"done": total, "total": total, "pct": 100},
                        stats=T.stats(msgs, decoded), frontier_ready=settings.frontier_ready())
    except Exception as e:
        jobs.set_status(job_id, state="error", message=f"preprocess failed: {e}")


def _image_files_for_ids(job_id: str, ids):
    """Map frontier-selected message ids -> the image/sticker files attached to them."""
    mp = jobs.path(job_id, "messages.json")
    if not mp.exists():
        return []
    want = set(ids)
    names = [fn for m in json.loads(mp.read_text()) if m.get("id") in want for fn in (m.get("media") or [])]
    files = []
    for nm in names:
        hits = list(jobs.path(job_id, "export").rglob(nm))
        if hits and decode.file_type(hits[0]) in ("image", "sticker"):
            files.append(hits[0])
    return files


def _read(job_id: str):
    """Read the chat, letting the frontier model request a deeper look at images.
    Default: one round (cheap-all already decoded). ITERATIVE_DISCOVERY: text-first,
    up to MAX_INSPECT_ROUNDS rounds, capped at MAX_INSPECT_IMAGES total."""
    try:
        st = jobs.get_status(job_id) or {}
        me = st.get("me", "me")
        route = settings.route(st.get("route"))
        if route is None:
            jobs.set_status(job_id, state="needs_config", message=settings.frontier_hint()); return

        def glimpse(done, total, item):              # reuse the live media feed during the loop
            cur = jobs.get_status(job_id) or {}
            recent = cur.get("recent", [])
            if item and item.get("caption"):
                recent = (recent + [item])[-12:]
            jobs.set_status(job_id, recent=recent)

        def stream_read(src_text: str, select_k: int):
            """Run one streamed read. Resets the live channels, then pushes throttled
            token updates — the model's process into status.partial_thinking (real
            reasoning if streamed, else the NOTE working-lines) and the analysis into
            status.partial_read. Returns (read_body, picks)."""
            jobs.set_status(job_id, partial_read="", partial_thinking="")
            ch = {"read": {"len": 0, "t": 0.0}, "thinking": {"len": 0, "t": 0.0}}

            def on_delta(kind: str, text_so_far: str):
                s = ch["read" if kind == "read" else "thinking"]
                field = "partial_read" if kind == "read" else "partial_thinking"
                now = time.monotonic()
                if len(text_so_far) > s["len"] and (s["len"] == 0 or now - s["t"] >= 0.2):
                    s["len"], s["t"] = len(text_so_far), now
                    jobs.set_status(job_id, **{field: text_so_far})

            raw = frontier.read(src_text, me, route, select_k=select_k, on_delta=on_delta)
            notes, body, picks = frontier.split_stream_read(raw)
            # final clean flush: the read body always; thinking only if NOTE-derived
            # (don't clobber a streamed reasoning trace with an empty notes string).
            flush = {"partial_read": body}
            if notes:
                flush["partial_thinking"] = notes
            jobs.set_status(job_id, **flush)
            return body, picks

        rounds = settings.max_inspect_rounds if settings.iterative_discovery else 1
        max_imgs = settings.max_inspect_images if settings.iterative_discovery else settings.deep_select_k
        batch = settings.deep_select_k
        text = jobs.path(job_id, "transcript.txt").read_text()
        jobs.set_status(job_id, state="analyzing", route=route.id, model=route.model,
                        partial_read="", partial_thinking="",
                        message="the frontier model is reading your chat…")

        seen, inspected, first_read, final, last_added = set(), [], None, None, False
        for _ in range(rounds):
            rem = max_imgs - len(seen)
            rd, picks = stream_read(text, select_k=min(batch, rem) if rem > 0 else 0)
            if first_read is None:
                first_read = rd
            final, last_added = rd, False
            picks = [i for i in dict.fromkeys(picks) if i not in seen][:rem]
            files = _image_files_for_ids(job_id, picks) if picks else []
            if not files:
                break                                # the model is satisfied (or nothing to fetch)
            jobs.set_status(job_id, message=f"opening {len(files)} image(s) the read flagged…")
            deep = decode.decode_deep(files, jobs.path(job_id, "work"), on_progress=glimpse)
            media = json.loads(jobs.path(job_id, "media.json").read_text())
            for name, rec in deep.items():
                media.setdefault(name, {}).update(rec)
            jobs.path(job_id, "media.json").write_text(json.dumps(media, ensure_ascii=False, indent=2))
            msgs = [ingest.Message(**m) for m in json.loads(jobs.path(job_id, "messages.json").read_text())]
            text = T.assemble(msgs, media)
            jobs.path(job_id, "transcript.txt").write_text(text)
            seen.update(picks)
            inspected += [n for n, rc in deep.items() if rc.get("caption")]
            last_added = True
            if len(seen) >= max_imgs:
                break
        if last_added:                               # re-read once on the freshly-enriched transcript
            jobs.set_status(job_id, message="re-reading with the photos in view…")
            final, _ = stream_read(text, select_k=0)

        jobs.path(job_id, "read.json").write_text(json.dumps(
            {"me": me, "read": final, "citations": frontier.citations(final),
             "route": route.id, "model": route.model,
             "first_read": first_read, "inspected": inspected, "deep_count": len(inspected)},
            ensure_ascii=False, indent=2))
        deletion = None
        if settings.ephemeral:
            jobs.delete_raw(job_id)
            deletion = {"raw_media_deleted_at": time.strftime("%H:%M:%S"),
                        "transcript_deleted_at": time.strftime("%H:%M:%S")}
        jobs.set_status(job_id, state="done", message="read ready",
                        deletion=deletion, deep_count=len(inspected), retained=jobs.retained(job_id))
    except frontier.NotConfigured as e:
        jobs.set_status(job_id, state="needs_config", message=str(e))
    except Exception as e:
        jobs.set_status(job_id, state="error", message=f"read failed: {e}")


# ---- API ----
@app.get("/api/config")
def get_config():
    return {"hosted": settings.hosted, "frontier_ready": settings.frontier_ready(),
            "routes": settings.public_routes(), "default_route": settings.default_route_id()}


@app.post("/api/upload")
async def upload(bg: BackgroundTasks, file: UploadFile, source: str = Form("whatsapp")):
    jid = jobs.create(source=source)
    zp = jobs.path(jid, "upload.zip")
    zp.write_bytes(await file.read())
    ingest.unzip(zp, jobs.path(jid, "export"))
    zp.unlink(missing_ok=True)
    bg.add_task(_preprocess, jid)
    return {"job_id": jid}


@app.get("/api/jobs/{job_id}")
def status(job_id: str):
    s = jobs.get_status(job_id)
    if not s:
        raise HTTPException(404)
    return s


@app.post("/api/jobs/{job_id}/role")
def set_role(job_id: str, me: str = Form(...)):
    if not jobs.exists(job_id):
        raise HTTPException(404)
    jobs.set_status(job_id, me=me)
    return {"ok": True, "me": me}


@app.get("/api/jobs/{job_id}/transcript", response_class=HTMLResponse)
def get_transcript(job_id: str):
    p = jobs.path(job_id, "transcript.txt")
    if not p.exists():
        raise HTTPException(404, "not available")
    return f"<pre>{p.read_text()}</pre>"


@app.post("/api/jobs/{job_id}/send")
def send(job_id: str, bg: BackgroundTasks, route: str = Form(None)):
    """Cross the boundary. `route` (optional) is the read backend the user picked;
    omitted -> the default route. The chosen route is recorded on the job."""
    if not jobs.path(job_id, "transcript.txt").exists():
        raise HTTPException(409, "transcript not ready")
    chosen = settings.route(route)
    if route and chosen is None:
        raise HTTPException(400, f"unknown route: {route}")
    if chosen is None:
        raise HTTPException(409, "no read route configured")
    jobs.set_status(job_id, route=chosen.id)
    bg.add_task(_read, job_id)
    return {"ok": True, "route": chosen.id}


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
            rich.append({"file": f, "type": rec.get("type") or decode.file_type(Path(f)),
                         "caption": decode._caption_of(rec) if rec else None})
        out.append({"id": m["id"], "ts": m["ts"], "sender": m["sender"],
                    "text": m["text"], "media": rich})
    return out


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
