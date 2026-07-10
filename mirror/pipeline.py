"""The pipeline orchestrator — owns a job from upload to finished read.

Two user-switchable modes (config.MODES), one engine:

  FAST (default) — text-first. No upfront media decode: a metadata manifest
  (manifest.py) turns media into informative placeholders, the frontier reads
  the text, REASONS about which media would change the read, and requests them
  ({ids, reason} — the reasons surface live in the UI). Only those are decoded
  locally, then one final read on the enriched transcript.

  DEEP — everything decodes, in parallel with the read. A producer thread works
  through the corpus (speech first — highest signal per second), appending each
  decoded item to evidence.jsonl; the read starts on the placeholder text
  immediately and periodically FOLDS batches of new evidence into a working
  draft (confirm / revise / extend), then a final pass re-grounds the draft on
  the fully-enriched transcript.

Both modes route giant chats (budget.plan tier 3) through chronological
map-reduce: era reads → synthesis. Fast fulfils per-era requests; deep's eras
naturally see whatever the producer has decoded by the time each is read, and
eras that gained evidence after their read are re-read before synthesis.

Phases (status.phase, machine-readable for the UI):
    parsing → manifest → reading → requesting → decoding → folding → composing → done

The privacy boundary is unchanged: decode is local; only assembled TEXT crosses.
"""

import json
import os
import threading
import time

from . import budget, decode, ingest, jobs, manifest, mediatypes, protocol, provider
from . import transcript as T
from .config import MODES, settings


def run(job_id: str):
    """Background entrypoint (wired to both upload paths)."""
    try:
        _run(job_id)
    except provider.NotConfigured as e:
        jobs.set_status(job_id, state="needs_config", message=str(e))
    except Exception as e:
        jobs.set_status(job_id, state="error", message=f"read failed: {e}")


def _run(job_id: str):
    st = jobs.get_status(job_id) or {}
    source = st.get("source") or "whatsapp"
    lang = st.get("lang")
    mode_name = st.get("mode") if st.get("mode") in MODES else "fast"
    mode = MODES[mode_name]
    route = settings.route()
    if route is None:
        jobs.set_status(job_id, state="needs_config", message=settings.frontier_hint())
        return

    # --- parsing -----------------------------------------------------------------
    jobs.set_status(job_id, state="inspecting", phase="parsing", mode=mode_name,
                    message="parsing your chat on this machine…", recent=[],
                    media_requests=None, fold=None, decode=None, decode_done=None,
                    progress={"done": 0, "total": 0, "pct": None})
    exp = jobs.path(job_id, "export")
    chat = ingest.find_export(exp, source)
    if not chat:
        want = "result.json" if source == "telegram" else "_chat.txt"
        jobs.set_status(job_id, state="error", message=f"no {want} found in the upload")
        return
    msgs, predecoded = ingest.parse_export(chat, source)
    if not msgs:
        jobs.set_status(job_id, state="error", message="couldn't read any messages from this export")
        return
    jobs.path(job_id, "messages.json").write_text(json.dumps(
        [{"id": m.id, "ts": m.ts, "sender": m.sender, "text": m.text, "media": m.media} for m in msgs],
        ensure_ascii=False))
    jobs.set_status(job_id, participants=ingest.participants(msgs))

    # --- manifest (metadata only — seconds of work, no models) --------------------
    jobs.set_status(job_id, phase="manifest", message="sizing your media (metadata only, on this machine)…")
    files = [f for f in ingest.media_files(exp, source) if mediatypes.kind(f) in mediatypes.DECODABLE]
    media = manifest.build(files)
    media.update(predecoded)               # Telegram .tgs emoji captions need no VLM
    jobs.path(job_id, "manifest.json").write_text(json.dumps(media, ensure_ascii=False))
    _write_media(job_id, media)

    # Transcription language: detect once from the chat's own text (language-
    # general; fixes short-clip misdetects), unless whisper_language forces one.
    wl = settings.whisper_language.strip().lower()
    language = wl if (wl and wl != "auto") else (
        None if wl == "auto" else decode.detect_language(" ".join(m.text for m in msgs if m.text)))

    ctx = {
        "job_id": job_id, "msgs": msgs, "media": media, "files": files,
        "route": route, "lang": lang, "language": language,
        "mode": mode, "mode_name": mode_name,
        "msgs_by_id": {m.id: m for m in msgs},
        "path_of": {f.name: f for f in files},
        "file_ids": _file_ids_map(msgs),
        "dropped": [0], "inspected": [],
    }

    plan = budget.plan(msgs, media)
    jobs.set_status(job_id, state="analyzing", route=route.id, model=route.model,
                    partial_read="", partial_thinking="",
                    plan={"tier": plan["tier"], "chunks": len(plan["chunks"]),
                          "est_tokens": plan["est_compact"], "script": plan["script"]})

    if mode_name == "deep":
        final = _run_deep(ctx, plan)
    else:
        final = _run_fast(ctx, plan)
    _persist(ctx, final)


# --- shared helpers ------------------------------------------------------------------


def _file_ids_map(msgs) -> dict:
    """basename → [message ids] (a file can, in odd exports, appear twice)."""
    out = {}
    for m in msgs:
        for f in m.media or []:
            out.setdefault(os.path.basename(f), []).append(m.id)
    return out


def _write_media(job_id, media):
    jobs.path(job_id, "media.json").write_text(json.dumps(media, ensure_ascii=False, indent=2))


def _read_media(job_id) -> dict:
    p = jobs.path(job_id, "media.json")
    return json.loads(p.read_text()) if p.exists() else {}


def _mk_on_delta(job_id):
    """A fresh throttled stream sink → status.partial_read / partial_thinking."""
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


def _stream_read(ctx, text, select_k, targets=protocol.DEFAULT_TARGETS):
    """One streamed full read → (body, requests)."""
    user = protocol.user_prompt(text, lang=ctx["lang"], select_k=select_k,
                                notes=not settings.stream_reasoning, targets=targets)
    raw = provider.complete(protocol.SOUL, user, ctx["route"],
                            on_event=protocol.stream_router(_mk_on_delta(ctx["job_id"])),
                            mock_reply=lambda _u: protocol.mock_read(text, select_k))
    out = protocol.parse(raw)
    flush = {"partial_read": out.body}
    if out.notes:                                # don't clobber a streamed reasoning trace
        flush["partial_thinking"] = out.notes
    jobs.set_status(ctx["job_id"], **flush)
    return out.body, out.requests


def _era_read(ctx, slice_text, part, total, select_k):
    """One blocking era read → (validated body, requests)."""
    user = protocol.era_prompt(slice_text, part, total, select_k=select_k,
                               targets=protocol.UNDECODED_TARGETS)
    raw = provider.complete(
        protocol.SOUL, user, ctx["route"],
        mock_reply=lambda _u: protocol.mock_era(_u, transcript=slice_text, select_k=select_k))
    out = protocol.parse(raw)
    # Validate here, not only at the end: an invented id must not survive the
    # era→synthesis hop and resurface in the final read.
    body, _, dr = protocol.validate_citations(out.body, len(ctx["msgs"]))
    ctx["dropped"][0] += dr
    return body, out.requests


def _glimpse(ctx, name, rec):
    cur = jobs.get_status(ctx["job_id"]) or {}
    recent = cur.get("recent", [])
    item = {"file": name, "type": rec.get("type", "image"), "caption": decode._caption_of(rec)}
    if item["caption"]:
        recent = (recent + [item])[-12:]
        jobs.set_status(ctx["job_id"], recent=recent)


def _publish_requests(ctx, requests):
    pub = [{"ids": r.ids, "kind": r.kind, "reason": r.reason, "status": r.status}
           for r in requests]
    jobs.set_status(ctx["job_id"], media_requests=pub)


def _fulfil(ctx, requests, max_items, max_audio_s):
    """Decode what the read asked for, under the mode envelope: item cap, audio-
    seconds cap (durations known from the manifest), wall-clock deadline. Speech
    batches share ONE Whisper load. Returns the filenames that gained content."""
    job_id, media, mode = ctx["job_id"], ctx["media"], ctx["mode"]
    speech, visual = [], []
    audio_s, chosen = 0.0, set()
    for r in requests:
        got = False
        kinds = []
        for i in r.ids:
            m = ctx["msgs_by_id"].get(i)
            for nm in (m.media if m else []) or []:
                f = ctx["path_of"].get(os.path.basename(nm))
                if f is None or f.name in chosen or f.suffix.lower() == ".tgs":
                    continue
                rec = media.get(f.name) or {}
                if rec.get("explicit") or rec.get("caption") or rec.get("transcript"):
                    continue                     # never deepen an explicit file; skip decoded
                if len(chosen) >= max_items:
                    continue
                k = mediatypes.kind(f)
                kinds.append(k)
                if k in ("audio", "video"):
                    dur = rec.get("seconds") or 30.0
                    if audio_s + dur > max_audio_s:
                        continue
                    audio_s += dur
                    speech.append(f)
                else:
                    visual.append(f)
                chosen.add(f.name)
                got = True
        r.kind = kinds[0] if kinds else ""
        r.status = "decoding" if got else "skipped"
    _publish_requests(ctx, requests)
    if not chosen:
        return []

    total = len(speech) + len(visual)
    jobs.set_status(job_id, phase="decoding",
                    message=f"decoding the {total} item(s) the read asked for",
                    progress={"done": 0, "total": total, "pct": 0})
    t0 = time.monotonic()
    state = {"done": 0}

    def on_item(name, rec, _done, _total):
        rec = {k: v for k, v in rec.items() if k != "_t"}
        media.setdefault(name, {}).update(rec)
        # escalated clips re-emit a corrected transcript — don't count past total
        state["done"] = min(state["done"] + 1, total)
        eta = None
        if state["done"] < total:
            eta = round((time.monotonic() - t0) / state["done"] * (total - state["done"]))
        jobs.set_status(job_id, progress={"done": state["done"], "total": total,
                                          "pct": int(state["done"] * 100 / total)},
                        eta_seconds=eta)
        _glimpse(ctx, name, rec)

    deadline = time.monotonic() + mode.decode_wall_s
    decode.decode_speech(speech, jobs.path(job_id, "work"), language=ctx["language"],
                         on_item=on_item, deadline=deadline)
    decode.decode_images(visual, jobs.path(job_id, "work"), deep=True,
                         on_item=on_item, deadline=deadline)
    _write_media(job_id, media)
    jobs.set_status(job_id, eta_seconds=None)

    done_names = [n for n in chosen
                  if any((media.get(n) or {}).get(k) for k in ("caption", "transcript", "frame_captions", "explicit"))]
    for r in requests:
        if r.status == "decoding":
            r.status = "done"
    _publish_requests(ctx, requests)
    return done_names


# --- FAST mode ------------------------------------------------------------------------


def _run_fast(ctx, plan):
    job_id, msgs, media, mode = ctx["job_id"], ctx["msgs"], ctx["media"], ctx["mode"]

    if plan["tier"] >= 3:
        return _fast_mapreduce(ctx, plan)

    header = manifest.header(msgs, media)
    text = T.assemble_for_read(msgs, media, header=header)
    jobs.path(job_id, "transcript.txt").write_text(text)
    jobs.set_status(job_id, phase="reading",
                    message="the frontier model is reading your chat (text first)…")
    k = mode.max_request_items if mode.request_rounds else 0
    final, requests = _stream_read(ctx, text, select_k=k, targets=protocol.UNDECODED_TARGETS)

    if requests:
        jobs.set_status(job_id, phase="requesting",
                        message="the read chose which media to open…")
        _publish_requests(ctx, requests)
        names = _fulfil(ctx, requests, mode.max_request_items, mode.max_request_audio_s)
        if names:
            ctx["inspected"] += names
            text = T.assemble_for_read(msgs, media, header=manifest.header(msgs, media))
            jobs.path(job_id, "transcript.txt").write_text(text)
            jobs.set_status(job_id, phase="composing",
                            message="re-reading with the decoded media in view…")
            final, _ = _stream_read(ctx, text, select_k=0)
    return final


def _fast_mapreduce(ctx, plan):
    job_id, msgs, media, mode = ctx["job_id"], ctx["msgs"], ctx["media"], ctx["mode"]
    chunks = plan["chunks"]
    n = len(chunks)
    jobs.set_status(job_id, message=f"this chat is large — reading it in {n} chronological passes…")
    eras = []
    remaining = mode.max_request_items
    read_t0 = time.monotonic()
    for idx, (s, e) in enumerate(chunks):
        span = f"{(msgs[s].ts or '')[:10]} → {(msgs[e - 1].ts or '')[:10]}"
        eta = round((time.monotonic() - read_t0) / idx * (n - idx + 1)) if idx else None
        jobs.set_status(job_id, phase="reading", eta_seconds=eta,
                        partial_thinking=f"reading era {idx + 1}/{n}  ({span})…")
        era_msgs = msgs[s:e]
        slice_text, _ = T.assemble_compact(era_msgs, media)
        k = min(mode.era_request_items, remaining) if mode.request_rounds else 0
        body, requests = _era_read(ctx, slice_text, idx + 1, n, select_k=k)
        if requests and k:
            jobs.set_status(job_id, phase="requesting",
                            message=f"era {idx + 1}/{n}: the read chose media to open…")
            names = _fulfil(ctx, requests, k, mode.era_request_audio_s)
            remaining -= len(names)
            if names:
                ctx["inspected"] += names
                slice_text, _ = T.assemble_compact(era_msgs, media)
                body, _ = _era_read(ctx, slice_text, idx + 1, n, select_k=0)
        eras.append((span, body))
        jobs.set_status(job_id, partial_read="\n\n".join(
            f"[era {i + 1}/{n} · {lab}]\n{t}" for i, (lab, t) in enumerate(eras)))
    jobs.path(job_id, "transcript.txt").write_text(T.assemble_for_read(msgs, media))
    return _synthesize(ctx, eras)


def _synthesize(ctx, eras):
    job_id = ctx["job_id"]
    jobs.set_status(job_id, phase="composing", eta_seconds=None,
                    message=f"synthesising the arc across {len(eras)} eras…")
    raw = provider.complete(protocol.SOUL, protocol.synth_prompt(eras, ctx["lang"]), ctx["route"],
                            on_event=protocol.stream_router(_mk_on_delta(job_id)),
                            mock_reply=protocol.mock_era)
    final = protocol.parse(raw).body
    jobs.set_status(job_id, partial_read=final)
    return final


# --- DEEP mode ------------------------------------------------------------------------


def _decode_producer(ctx):
    """The background decode worker: speech first (highest signal per second),
    then images/stickers/video-keyframes. Each item lands in evidence.jsonl and
    the shared media dict; media.json snapshots every 25 items; decode counters
    (and the tiered-ASR second-look counter) stream to the status file. Fail-open:
    an exception ends decode but the text read still completes."""
    job_id, media, files = ctx["job_id"], ctx["media"], ctx["files"]
    try:
        work = jobs.path(job_id, "work")
        speech = [f for f in files if mediatypes.kind(f) in ("audio", "video")]
        visual = [f for f in files if mediatypes.kind(f) in ("image", "sticker", "video")]
        visual = [f for f in visual
                  if not (media.get(f.name) or {}).get("caption")      # skip predecoded .tgs
                  and f.suffix.lower() != ".tgs"]
        if settings.hosted:
            cap = 1500                                # hosted deep-corpus cap (honest: annotated)
            budget_left = cap - len(speech)
            if budget_left < len(visual):
                skipped = len(visual) - max(0, budget_left)
                visual = visual[:max(0, budget_left)]
                print(f"[deep] hosted corpus cap: skipping {skipped} visual item(s)", flush=True)
        total = len(speech) + len(visual)
        if not total:
            jobs.set_status(job_id, decode_done=True)
            return
        t0 = time.monotonic()
        state = {"done": 0, "reinspect": None}

        def _decode_status(eta=None):
            jobs.set_status(job_id, decode={"done": state["done"], "total": total,
                                            "reinspect": state["reinspect"],
                                            "eta_seconds": eta})

        def emit(name, rec, _done, _total):
            rec = {k: v for k, v in rec.items() if k != "_t"}
            cur = media.setdefault(name, {})
            cur.update(rec)
            state["done"] = min(state["done"] + 1, total)
            jobs.append_evidence(job_id, {"file": name,
                                          "ids": ctx["file_ids"].get(os.path.basename(name), []),
                                          "rec": cur})
            if state["done"] % 25 == 0 or state["done"] >= total:
                _write_media(job_id, media)
            eta = None
            if state["done"] < total:
                eta = round((time.monotonic() - t0) / state["done"] * (total - state["done"]))
            _decode_status(eta)
            _glimpse(ctx, name, rec)

        def on_reinspect(rdone, rtotal, item):
            state["reinspect"] = {"done": rdone, "total": rtotal}
            _decode_status()
            if item:
                _glimpse(ctx, item.get("file"), {"type": item.get("type"),
                                                 "caption": item.get("caption")})

        _decode_status()
        decode.decode_speech(speech, work, language=ctx["language"],
                             on_item=emit, on_reinspect=on_reinspect)
        decode.decode_images(visual, work, deep=False, on_item=emit)
        _write_media(job_id, media)
        jobs.set_status(job_id, decode_done=True)
        print(f"[deep] decode producer finished: {state['done']}/{total} in "
              f"{time.monotonic() - t0:.0f}s", flush=True)
    except Exception as e:
        print(f"[deep] decode producer FAILED (read continues on text): {e}", flush=True)
        _write_media(job_id, media)
        jobs.set_status(job_id, decode_done=True, decode_error=str(e)[:200])


def _run_deep(ctx, plan):
    job_id = ctx["job_id"]
    producer = threading.Thread(target=_decode_producer, args=(ctx,), daemon=True)
    producer.start()
    if plan["tier"] >= 3:
        return _deep_mapreduce(ctx, plan)
    return _deep_fold(ctx)


def _deep_fold(ctx):
    """DEEP, one-window chats: text-first read, then fold rounds over the evidence
    stream, then a final re-grounding pass on the fully-enriched transcript."""
    job_id, msgs, mode = ctx["job_id"], ctx["msgs"], ctx["mode"]
    n_msgs = len(msgs)

    header = manifest.header(msgs, ctx["media"])
    text = T.assemble_for_read(msgs, ctx["media"], header=header)
    jobs.path(job_id, "transcript.txt").write_text(text)
    jobs.set_status(job_id, phase="reading",
                    message="the frontier model is reading your chat while the media decodes…")
    draft, _ = _stream_read(ctx, text, select_k=0)
    draft, _, dr = protocol.validate_citations(draft, n_msgs)
    ctx["dropped"][0] += dr

    consumed, rounds = 0, 0
    last_fold = time.monotonic()
    while True:
        st = jobs.get_status(job_id) or {}
        items = jobs.read_evidence(job_id, consumed)
        done = bool(st.get("decode_done"))
        idle = time.monotonic() - last_fold
        fire = (len(items) >= mode.fold_min_items
                or (done and items)
                or (idle >= mode.fold_min_interval_s and len(items) >= mode.fold_trickle_items))
        if fire:
            batch = items[:mode.fold_max_per_round]
            consumed += len(batch)
            rounds += 1
            total_ev = (st.get("decode") or {}).get("total")
            # `consumed` counts evidence LINES; escalated clips re-emit a corrected
            # line, so it can exceed the item total — clamp the display.
            jobs.set_status(job_id, phase="folding",
                            fold={"round": rounds,
                                  "evidence_seen": min(consumed, total_ev) if total_ev else consumed,
                                  "evidence_total": total_ev},
                            message=f"revising the read with {len(batch)} new pieces of evidence…")
            ev = T.render_evidence(ctx["msgs_by_id"], batch)
            if ev:
                raw = provider.complete(
                    protocol.SOUL, protocol.fold_prompt(draft, ev, ctx["lang"]), ctx["route"],
                    on_event=protocol.stream_router(_mk_on_delta(job_id)),
                    mock_reply=protocol.mock_era)
                body = protocol.parse(raw).body
                if body.strip():
                    draft, _, dr = protocol.validate_citations(body, n_msgs)
                    ctx["dropped"][0] += dr
            last_fold = time.monotonic()
            continue
        if done and not items:
            break
        time.sleep(5)

    ctx["media"] = _read_media(job_id)
    ctx["inspected"] = [n for n, r in ctx["media"].items()
                        if r.get("caption") or r.get("transcript") or r.get("frame_captions")]
    text = T.assemble_for_read(msgs, ctx["media"])
    jobs.path(job_id, "transcript.txt").write_text(text)
    jobs.set_status(job_id, phase="composing",
                    message="composing the final read on the fully-decoded chat…")
    user = protocol.deep_final_prompt(text, draft, ctx["lang"], notes=not settings.stream_reasoning)
    raw = provider.complete(protocol.SOUL, user, ctx["route"],
                            on_event=protocol.stream_router(_mk_on_delta(job_id)),
                            mock_reply=lambda _u: protocol.mock_read(text))
    out = protocol.parse(raw)
    return out.body or draft


def _deep_mapreduce(ctx, plan):
    """DEEP, giant chats: era reads proceed text-first against the CURRENT decode
    snapshot (natural partial fold-in, zero extra calls); after decode completes,
    eras that gained enough evidence are re-read; then synthesis."""
    job_id, msgs, mode = ctx["job_id"], ctx["msgs"], ctx["mode"]
    chunks = plan["chunks"]
    n = len(chunks)
    jobs.set_status(job_id, message=f"this chat is large — reading it in {n} chronological passes "
                                    "while the media decodes…")
    eras, marks = [], []
    read_t0 = time.monotonic()
    for idx, (s, e) in enumerate(chunks):
        span = f"{(msgs[s].ts or '')[:10]} → {(msgs[e - 1].ts or '')[:10]}"
        eta = round((time.monotonic() - read_t0) / idx * (n - idx + 1)) if idx else None
        jobs.set_status(job_id, phase="reading", eta_seconds=eta,
                        partial_thinking=f"reading era {idx + 1}/{n}  ({span})…")
        media_now = _read_media(job_id)
        slice_text, _ = T.assemble_compact(msgs[s:e], media_now)
        body, _ = _era_read(ctx, slice_text, idx + 1, n, select_k=0)
        eras.append([span, body])
        marks.append(len(jobs.read_evidence(job_id, 0)))
        jobs.set_status(job_id, partial_read="\n\n".join(
            f"[era {i + 1}/{n} · {lab}]\n{t}" for i, (lab, t) in enumerate(eras)))

    while not (jobs.get_status(job_id) or {}).get("decode_done"):
        jobs.set_status(job_id, phase="decoding", eta_seconds=None,
                        message="the eras are read — waiting for the media decode to finish…")
        time.sleep(5)

    all_items = jobs.read_evidence(job_id, 0)
    media_final = _read_media(job_id)
    ctx["media"] = media_final
    ctx["inspected"] = [n2 for n2, r in media_final.items()
                        if r.get("caption") or r.get("transcript") or r.get("frame_captions")]
    for idx, (s, e) in enumerate(chunks):
        gained = sum(1 for it in all_items[marks[idx]:]
                     if any(s <= i < e for i in (it.get("ids") or [])))
        if gained >= mode.era_reread_threshold:
            jobs.set_status(job_id, phase="folding",
                            message=f"re-reading era {idx + 1}/{n} with {gained} decoded media in view…")
            slice_text, _ = T.assemble_compact(msgs[s:e], media_final)
            eras[idx][1], _ = _era_read(ctx, slice_text, idx + 1, n, select_k=0)

    jobs.path(job_id, "transcript.txt").write_text(T.assemble_for_read(msgs, media_final))
    return _synthesize(ctx, [tuple(e) for e in eras])


# --- persist --------------------------------------------------------------------------


def _persist(ctx, final):
    job_id, route = ctx["job_id"], ctx["route"]
    final, citations, dropped = protocol.validate_citations(final or "", len(ctx["msgs"]))
    dropped += ctx["dropped"][0]
    if dropped:
        print(f"[read] stripped {dropped} invalid citation id(s)", flush=True)

    # Self-destruct: the read is READY — its TTL countdown starts now (hosted tier).
    expires_at = (time.time() + settings.read_ttl_seconds) if settings.hosted else None
    jobs.path(job_id, "read.json").write_text(json.dumps(
        {"read": final, "citations": citations, "citations_dropped": dropped,
         "route": route.id, "model": route.model, "mode": ctx["mode_name"],
         "expires_at": expires_at, "inspected": ctx["inspected"],
         "deep_count": len(ctx["inspected"]),
         # honest provenance: the date window the user cut the export down to
         "slice_range": (jobs.get_status(job_id) or {}).get("slice_range")},
        ensure_ascii=False, indent=2))
    if settings.ephemeral:
        jobs.delete_raw(job_id)
    jobs.set_status(job_id, state="done", phase="done", message="read ready",
                    expires_at=expires_at, eta_seconds=None,
                    deep_count=len(ctx["inspected"]), retained=jobs.retained(job_id))
