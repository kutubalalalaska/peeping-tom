"""FastAPI server — the whole web layer. Uses only internal modules.

API:
    GET    /api/config                  {hosted, frontier_ready, routes[], default_route, read_ttl_seconds}
    GET    /api/quota                   reads left for this cookie-session (hosted tier)
    POST   /api/upload                  zip + source -> job -> LOCAL preprocess (background)
    GET    /api/jobs/{id}               status (poll): state, progress, recent…
    GET    /api/jobs/{id}/transcript    the exact text that crossed the boundary
    GET    /api/jobs/{id}/result        the read JSON (+ resolved citations)
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

from . import ingest, decode, transcript as T, jobs, budget, mediatypes, protocol, provider, uploads
from .config import settings

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


# ---- pipeline phases (background) ----
def _preprocess(job_id: str):
    """Parse first (instant), then decode media (slow), streaming progress +
    glimpses. v1: the upload itself drives the whole sequence — when the transcript
    is assembled this chains straight into the read (no identity pick, no manual
    send). `participants` is still computed for v2 (who's-who, deferred)."""
    try:
        source = (jobs.get_status(job_id) or {}).get("source") or "whatsapp"
        exp = jobs.path(job_id, "export")
        chat = ingest.find_export(exp, source)
        if not chat:
            want = "result.json" if source == "telegram" else "_chat.txt"
            jobs.set_status(job_id, state="error", message=f"no {want} found in the upload"); return
        msgs, predecoded = ingest.parse_export(chat, source)
        if not msgs:
            jobs.set_status(job_id, state="error", message="couldn't read any messages from this export"); return

        media = ingest.media_files(exp, source)
        # Iterative discovery decodes SPEECH up front (voice notes + video messages —
        # text-first); images are decoded on demand during the read loop. Otherwise:
        # cheap-all decode now.
        to_decode = ([f for f in media if mediatypes.kind(f) in ("audio", "video")]
                     if settings.iterative_discovery else media)
        # Drop anything already captioned without the VLM (Telegram .tgs emoji stickers).
        to_decode = [f for f in to_decode if f.name not in predecoded]
        total = sum(1 for f in to_decode if mediatypes.kind(f) in mediatypes.DECODABLE)
        # Iterative discovery is text-first: the up-front pass transcribes speech (voice
        # notes + video messages); images are opened on demand during the read. Present it
        # as ONE "parsing" step — parse is already done; this is just speech (or instant).
        if settings.iterative_discovery:
            msg = ("parsing your chat and transcribing voice & video messages — on this machine…"
                   if total else "parsing your chat on this machine…")
        else:
            msg = "decoding your media on this machine…"
        jobs.set_status(job_id, state="inspecting", message=msg,
                        participants=ingest.participants(msgs), recent=[],
                        progress={"done": 0, "total": total, "pct": 0 if total else 100})

        decode_t0 = time.monotonic()

        def on_progress(done: int, total: int, item: dict):
            cur = jobs.get_status(job_id) or {}
            recent = cur.get("recent", [])
            if item and item.get("caption"):
                recent = (recent + [item])[-12:]
            pct = int(done * 100 / total) if total else 100
            # Live, self-correcting ETA for the (long-pole) transcription phase: once a few
            # items finish, remaining ≈ elapsed/done × items_left.
            eta = None
            if done and total and done < total:
                eta = round((time.monotonic() - decode_t0) / done * (total - done))
            jobs.set_status(job_id, progress={"done": done, "total": total, "pct": pct},
                            recent=recent, eta_seconds=eta, eta_phase="transcribing")

        # Tiered-ASR escalation reports on its OWN channel: a distinct phase with its own
        # counter (reinspect.done/total) so re-checked clips read as a second look, not a
        # duplicate first pass, and the main bar doesn't sit pinned at N/N while they flow.
        reins_t0 = []                                    # start time for the re-inspection ETA
        def on_reinspect(rdone: int, rtotal: int, item: dict):
            if rdone == 0 or not reins_t0:
                reins_t0[:] = [time.monotonic()]
            cur = jobs.get_status(job_id) or {}
            recent = cur.get("recent", [])
            if item and item.get("caption"):
                recent = (recent + [item])[-12:]         # item carries reinspected=True
            eta = None
            if rdone and rtotal and rdone < rtotal and reins_t0:
                eta = round((time.monotonic() - reins_t0[0]) / rdone * (rtotal - rdone))
            jobs.set_status(job_id,
                            message="taking another pass on a few clips for a clearer transcript…",
                            recent=recent, reinspect={"done": rdone, "total": rtotal},
                            eta_seconds=eta, eta_phase="reinspecting")

        # Transcription language (general, per-chat): explicit override / forced per-clip auto /
        # else detect from the chat's own TEXT and apply to all clips (no audience assumption).
        wl = settings.whisper_language.strip().lower()
        lang = wl if (wl and wl != "auto") else (None if wl == "auto"
                                                 else decode.detect_language(" ".join(m.text for m in msgs if m.text)))
        print(f"[decode] transcription language: {lang or 'auto (per-clip)'}", flush=True)
        decoded = decode.decode_media(to_decode, jobs.path(job_id, "work"), on_progress,
                                      language=lang, on_reinspect=on_reinspect)
        decoded.update(predecoded)   # fold in the VLM-free captions (Telegram emoji stickers)
        jobs.path(job_id, "media.json").write_text(json.dumps(decoded, ensure_ascii=False, indent=2))
        jobs.path(job_id, "transcript.txt").write_text(T.assemble_for_read(msgs, decoded))
        # structured transcript, keyed by id — powers clickable [#id] receipts
        jobs.path(job_id, "messages.json").write_text(json.dumps(
            [{"id": m.id, "ts": m.ts, "sender": m.sender, "text": m.text, "media": m.media} for m in msgs],
            ensure_ascii=False))
        jobs.set_status(job_id, message="starting the read…", reinspect=None,
                        progress={"done": total, "total": total, "pct": 100},
                        stats=T.stats(msgs, decoded), frontier_ready=settings.frontier_ready())
    except Exception as e:
        jobs.set_status(job_id, state="error", message=f"preprocess failed: {e}"); return
    # The upload starts everything: go straight into the read (default route). _read
    # has its own try/except + needs_config handling, so failures don't read as
    # "preprocess failed". A manual /send can still re-run it (e.g. another model).
    _read(job_id)


def _image_files_for_ids(job_id: str, ids):
    """Map frontier-selected message ids -> the image/sticker files attached to them."""
    mp = jobs.path(job_id, "messages.json")
    if not mp.exists():
        return []
    medp = jobs.path(job_id, "media.json")
    media = json.loads(medp.read_text()) if medp.exists() else {}
    want = set(ids)
    names = [fn for m in json.loads(mp.read_text()) if m.get("id") in want for fn in (m.get("media") or [])]
    files = []
    for nm in names:
        # Never deep-caption an explicit image: the DETAILED prompt would generate the
        # graphic description the neutral marker exists to keep off the boundary.
        rec = media.get(nm) or media.get(Path(nm).name) or {}
        if rec.get("explicit"):
            continue
        hits = list(jobs.path(job_id, "export").rglob(nm))
        # .tgs (Telegram animated stickers) carry no raster to deepen — they're
        # already captioned from the message emoji, so never send them for a closer look.
        if hits and mediatypes.kind(hits[0]) in ("image", "sticker") and hits[0].suffix.lower() != ".tgs":
            files.append(hits[0])
    return files


def _read(job_id: str):
    """Read the chat, letting the frontier model request a deeper look at images.
    Default: one round (cheap-all already decoded). ITERATIVE_DISCOVERY: text-first,
    up to MAX_INSPECT_ROUNDS rounds, capped at MAX_INSPECT_IMAGES total."""
    try:
        st = jobs.get_status(job_id) or {}
        me = st.get("me") or ""          # identity deferred to v2; the read isn't anchored to a name
        lang = st.get("lang")            # the read's output language (chosen UI language)
        route = settings.route(st.get("route"))
        if route is None:
            jobs.set_status(job_id, state="needs_config", message=settings.frontier_hint()); return

        def glimpse(done, total, item):              # reuse the live media feed during the loop
            cur = jobs.get_status(job_id) or {}
            recent = cur.get("recent", [])
            if item and item.get("caption"):
                recent = (recent + [item])[-12:]
            jobs.set_status(job_id, recent=recent)

        def mk_on_delta():
            """A fresh throttled stream sink → status.partial_read / partial_thinking
            (read body vs the model's live process). Resets both channels first."""
            jobs.set_status(job_id, partial_read="", partial_thinking="")
            ch = {"read": {"len": 0, "t": 0.0}, "thinking": {"len": 0, "t": 0.0}}

            def on_delta(kind: str, text_so_far: str):
                s = ch["read" if kind == "read" else "thinking"]
                field = "partial_read" if kind == "read" else "partial_thinking"
                now = time.monotonic()
                if len(text_so_far) > s["len"] and (s["len"] == 0 or now - s["t"] >= 0.2):
                    s["len"], s["t"] = len(text_so_far), now
                    jobs.set_status(job_id, **{field: text_so_far})
            return on_delta

        def stream_read(src_text: str, select_k: int):
            """One streamed one-shot read → (read_body, picked media ids)."""
            user = protocol.user_prompt(src_text, lang=lang, select_k=select_k,
                                        notes=not settings.stream_reasoning)
            raw = provider.complete(protocol.SOUL, user, route,
                                    on_event=protocol.stream_router(mk_on_delta()),
                                    mock_reply=lambda _u: protocol.mock_read(src_text, select_k))
            out = protocol.parse(raw)
            flush = {"partial_read": out.body}
            if out.notes:                            # don't clobber a streamed reasoning trace
                flush["partial_thinking"] = out.notes
            jobs.set_status(job_id, **flush)
            return out.body, [i for r in out.requests for i in r.ids]

        # Decide the read strategy (SCALING.md size gate): one-shot, or — for a corpus
        # too big for the context window — chronological MAP-REDUCE.
        msgs_all = [ingest.Message(**m) for m in json.loads(jobs.path(job_id, "messages.json").read_text())]
        media_all = (json.loads(jobs.path(job_id, "media.json").read_text())
                     if jobs.path(job_id, "media.json").exists() else {})
        plan = budget.plan(msgs_all, media_all)
        jobs.set_status(job_id, state="analyzing", route=route.id, model=route.model,
                        partial_read="", partial_thinking="",
                        plan={"tier": plan["tier"], "chunks": len(plan["chunks"]), "form": plan["form"],
                              "est_tokens": plan["est_compact"], "script": plan["script"]},
                        message="the frontier model is reading your chat…")

        inspected, first_read, final = [], None, None
        dropped_total = [0]                          # invalid citations stripped (observability)
        if plan["tier"] >= 3:
            # MAP-REDUCE (SCALING.md Stage 3): read chronological slices, then synthesise.
            # Stage 4: each era may deepen a few of ITS OWN images (budget distributed
            # across eras, not one flat global cap) — only in iterative mode, where images
            # arrive as placeholders the era can INSPECT.
            n = len(plan["chunks"])
            jobs.set_status(job_id, message=f"this chat is large — reading it in {n} chronological passes…")
            eras = []
            read_t0 = time.monotonic()
            for idx, (s, e) in enumerate(plan["chunks"]):
                span = f"{(msgs_all[s].ts or '')[:10]} → {(msgs_all[e - 1].ts or '')[:10]}"
                # Live read-phase ETA: avg time per finished era × eras left (+1 ≈ the synthesis).
                eta = round((time.monotonic() - read_t0) / idx * (n - idx + 1)) if idx else None
                jobs.set_status(job_id, partial_thinking=f"reading era {idx + 1}/{n}  ({span})…",
                                eta_seconds=eta, eta_phase="reading")
                era_msgs = msgs_all[s:e]
                slice_text, _ = T.assemble_compact(era_msgs, media_all)
                k = max(0, (min(settings.images_per_era, settings.max_inspect_images_total - len(inspected))
                            if settings.iterative_discovery else 0))

                def read_era(text, select_k):
                    user = protocol.era_prompt(text, idx + 1, n, select_k=select_k)
                    raw = provider.complete(
                        protocol.SOUL, user, route,
                        mock_reply=lambda _u: protocol.mock_era(_u, transcript=text, select_k=select_k))
                    out = protocol.parse(raw)
                    # Validate here, not just at the end: an invented id must not
                    # survive the era→synthesis hop and resurface in the final read.
                    body, _, dr = protocol.validate_citations(out.body, len(msgs_all))
                    dropped_total[0] += dr
                    return body, [i for r in out.requests for i in r.ids]

                era_text, picks = read_era(slice_text, select_k=k)
                files = _image_files_for_ids(job_id, picks) if picks else []
                if files:                                # deepen this era's flagged images, then re-read it
                    jobs.set_status(job_id, message=f"era {idx + 1}/{n}: opening {len(files)} image(s) the read flagged…")
                    deep = decode.decode_deep(files, jobs.path(job_id, "work"), on_progress=glimpse)
                    for name, rec in deep.items():
                        media_all.setdefault(name, {}).update(rec)
                    jobs.path(job_id, "media.json").write_text(json.dumps(media_all, ensure_ascii=False, indent=2))
                    inspected += [nm for nm, rc in deep.items() if rc.get("caption")]
                    slice_text, _ = T.assemble_compact(era_msgs, media_all)
                    era_text, _ = read_era(slice_text, select_k=0)
                eras.append((span, era_text))
                jobs.set_status(job_id, partial_read="\n\n".join(
                    f"[era {i + 1}/{n} · {lab}]\n{t}" for i, (lab, t) in enumerate(eras)))
            # the deepened captions changed the transcript — persist what actually crossed
            jobs.path(job_id, "transcript.txt").write_text(T.assemble_for_read(msgs_all, media_all))
            jobs.set_status(job_id, message=f"synthesising the arc across {n} eras…")
            raw = provider.complete(protocol.SOUL, protocol.synth_prompt(eras, lang), route,
                                    on_event=protocol.stream_router(mk_on_delta()),
                                    mock_reply=protocol.mock_era)
            final = protocol.parse(raw).body
            jobs.set_status(job_id, partial_read=final)
            first_read = final
        else:
            # ONE-SHOT (+ optional iterative image deepening) — the existing path.
            rounds = settings.max_inspect_rounds if settings.iterative_discovery else 1
            max_imgs = settings.max_inspect_images if settings.iterative_discovery else settings.deep_select_k
            batch = settings.deep_select_k
            text = jobs.path(job_id, "transcript.txt").read_text()
            seen, last_added = set(), False
            for _ in range(rounds):
                rem = max_imgs - len(seen)
                rd, picks = stream_read(text, select_k=min(batch, rem) if rem > 0 else 0)
                if first_read is None:
                    first_read = rd
                final, last_added = rd, False
                picks = [i for i in dict.fromkeys(picks) if i not in seen][:rem]
                files = _image_files_for_ids(job_id, picks) if picks else []
                if not files:
                    break                            # the model is satisfied (or nothing to fetch)
                jobs.set_status(job_id, message=f"opening {len(files)} image(s) the read flagged…")
                deep = decode.decode_deep(files, jobs.path(job_id, "work"), on_progress=glimpse)
                media = json.loads(jobs.path(job_id, "media.json").read_text())
                for name, rec in deep.items():
                    media.setdefault(name, {}).update(rec)
                jobs.path(job_id, "media.json").write_text(json.dumps(media, ensure_ascii=False, indent=2))
                msgs = [ingest.Message(**m) for m in json.loads(jobs.path(job_id, "messages.json").read_text())]
                text = T.assemble_for_read(msgs, media)
                jobs.path(job_id, "transcript.txt").write_text(text)
                seen.update(picks)
                inspected += [nm for nm, rc in deep.items() if rc.get("caption")]
                last_added = True
                if len(seen) >= max_imgs:
                    break
            if last_added:                           # re-read once on the freshly-enriched transcript
                jobs.set_status(job_id, message="re-reading with the photos in view…")
                final, _ = stream_read(text, select_k=0)

        # The citation choke point: every id the read cites must exist. Invented
        # ids are stripped from the text HERE, before anything is persisted — the
        # frontend never has to grey out or silently drop a chip again.
        final, citations, dropped = protocol.validate_citations(final or "", len(msgs_all))
        dropped += dropped_total[0]
        if dropped:
            print(f"[read] stripped {dropped} invalid citation id(s)", flush=True)

        # Self-destruct: the read is now READY, so start its TTL countdown here (not
        # at upload) — the result page counts down to this `expires_at`, after which
        # the sweeper deletes the whole job. Only on the hosted tier.
        expires_at = (time.time() + settings.read_ttl_seconds) if settings.hosted else None
        jobs.path(job_id, "read.json").write_text(json.dumps(
            {"me": me, "read": final, "citations": citations, "citations_dropped": dropped,
             "route": route.id, "model": route.model, "expires_at": expires_at,
             "first_read": first_read, "inspected": inspected, "deep_count": len(inspected)},
            ensure_ascii=False, indent=2))
        deletion = None
        if settings.ephemeral:
            jobs.delete_raw(job_id)
            deletion = {"raw_media_deleted_at": time.strftime("%H:%M:%S"),
                        "transcript_deleted_at": time.strftime("%H:%M:%S")}
        jobs.set_status(job_id, state="done", message="read ready", expires_at=expires_at, eta_seconds=None,
                        deletion=deletion, deep_count=len(inspected), retained=jobs.retained(job_id))
    except provider.NotConfigured as e:
        jobs.set_status(job_id, state="needs_config", message=str(e))
    except Exception as e:
        jobs.set_status(job_id, state="error", message=f"read failed: {e}")


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
            "read_ttl_seconds": settings.read_ttl_seconds}


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
                 source: str = Form("whatsapp"), lang: str = Form("en")):
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
    # The chosen UI language sets the read's OUTPUT language (frontier maps it via a
    # whitelist; an unknown code is ignored). Deliberately independent of Whisper.
    jobs.set_status(jid, lang=(lang or "en").split("-")[0].lower()[:5])
    zp = jobs.path(jid, "upload.zip")
    # Stream the upload to disk in chunks — a multi-GB export would otherwise load
    # whole into RAM via file.read() and risk OOM on a small Docker VM.
    with zp.open("wb") as out:
        while chunk := await file.read(4 * 1024 * 1024):
            out.write(chunk)
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
uploads.PREPROCESS = _preprocess


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
