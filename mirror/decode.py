"""Local media decoder — runs entirely on the machine (privacy boundary).

Two entrypoints, driven by the pipeline orchestrator:
  - decode_speech = Whisper for voice notes + video speech. ONE model load per
                    batch; tiered-ASR escalation (garbage transcripts re-run on
                    a bigger model, worst-first, capped) at batch end.
  - decode_images = the VLM for images/stickers (+ video keyframes on the cheap
                    pass). deep=False is the cheap-all pass (small px, one
                    classify+caption call); deep=True is the read-REQUESTED look
                    (full px, rich caption). NudeNet gates BOTH before any
                    caption call — explicit images carry a neutral marker only.

Speed levers: the image *encode* is the cost, so we (a) resize before the VLM,
(b) do ONE call per image, (c) keep the model warm and bound generation. The
captioner stays BLIND — it never sees surrounding messages, so each caption is
independent evidence.

Vision goes to the bundled Ollama service; audio uses faster-whisper. Both
entrypoints report per-item progress and honor an optional wall-clock deadline.
"""

import base64, gc, json, re, subprocess, time, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .config import settings
from .mediatypes import is_video_note, kind as file_type

# Register HEIC/HEIF support for Pillow if available (common in iPhone exports).
# Wheels bundle libheif, so this needs no system packages; absent it, .heic falls
# back to the original bytes in _prep_image (which the VLM can't decode).
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

# One pass: classify + caption. CAPTION length scales with content via the prompt.
# EXPLICIT is a yes/no classification (a much lighter ask than a graphic description,
# so the VLM complies far more readily) — when it's yes we DROP the caption and carry
# a neutral marker instead (see _decode_image), so nothing intimate crosses the boundary.
COMBINED = (
    "Describe this single chat image. Output exactly these two lines and nothing else:\n"
    "CATEGORY=<people|scene|screenshot|document|diagram|meme|other>; PEOPLE=<yes|no>; EXPLICIT=<yes|no>\n"
    "CAPTION=<text>\n"
    "Rules: PEOPLE=yes only if real human faces are visible. EXPLICIT=yes if the image "
    "contains nudity or sexual content, otherwise EXPLICIT=no. If CATEGORY is people or "
    "scene, CAPTION is 1-2 precise lines (what/who is shown, setting, visible text, mood); "
    "otherwise CAPTION is at most 8 words. Only what is visible — never guess identities."
)
# Deep pass: a rich description regardless of category (the read asked to see it).
DETAILED = ("Describe this chat image in 2-4 precise lines: what/who is shown, the setting, any "
            "visible text, the mood, and anything notable. Only what is visible — do not guess identities.")
# Deep pass, SELF-GUARDED: the rich description PLUS an explicit classification in one
# call, so the deep pass can drop an explicit image to the neutral marker on its own —
# the cheap-all EXPLICIT flag doesn't exist in iterative-discovery mode (images aren't
# cheap-all decoded), so the deep pass must not rely on it. EXPLICIT first (cheap to
# answer); CAPTION is discarded in code when EXPLICIT=yes (never produced for storage).
DEEP_GUARDED = (
    "Describe this single chat image. Output exactly these two lines and nothing else:\n"
    "EXPLICIT=<yes|no>\n"
    "CAPTION=<text>\n"
    "Rules: EXPLICIT=yes if the image contains nudity or sexual content, otherwise no. "
    "CAPTION is 2-4 precise lines: what/who is shown, the setting, any visible text, the mood, "
    "and anything notable. Only what is visible — do not guess identities.")
STICKER = "WhatsApp sticker. One short line: what it depicts and the emotion it conveys (stickers stand in for words)."
FRAME = "Frame from a short video in a chat. One line: what it shows."
RICH = {"people", "scene"}


def _vlm(img: Path, prompt: str, num_predict: int = None, model: str = None):
    """Run one vision call. Returns (text, elapsed_ms). The image-encode dominates,
    so callers pass an already-resized image, one prompt, and the right-sized model."""
    t0 = time.monotonic()
    if settings.vision_backend == "mock":
        import hashlib
        h = int(hashlib.md5(img.name.encode()).hexdigest(), 16)
        if "EXPLICIT=" in prompt:                      # combined classify+caption (cheap-all or deep-guard)
            people = "yes" if ("PHOTO" in img.name.upper() and h % 2 == 0) else "no"
            cat = "people" if people == "yes" else ("screenshot" if h % 3 else "document")
            explicit = "yes" if (people == "yes" and h % 5 == 0) else "no"   # exercise the marker path
            cap = f"[mock caption {img.stem[-6:]}]" if (cat in RICH or people == "yes") else f"mock {cat}"
            return f"CATEGORY={cat}; PEOPLE={people}; EXPLICIT={explicit}\nCAPTION={cap}", (time.monotonic() - t0) * 1000
        return f"[mock caption {img.stem[-6:]}]", (time.monotonic() - t0) * 1000

    b64 = base64.b64encode(img.read_bytes()).decode()
    options = {"temperature": 0}
    if num_predict:
        options["num_predict"] = num_predict
    payload = {"model": model or settings.vision_model, "prompt": prompt, "images": [b64],
               "stream": False, "keep_alive": settings.ollama_keep_alive, "options": options}
    req = urllib.request.Request(settings.ollama_host.rstrip("/") + "/api/generate",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=settings.vision_timeout) as r:
        text = json.loads(r.read())["response"].strip()
    return text, (time.monotonic() - t0) * 1000


def warm_up():
    """Preload the decode VLM into Ollama at boot so the FIRST image isn't a cold
    load (tens of seconds on CPU). Fires a promptless /api/generate — which just
    loads the model into memory — with the same keep_alive the real calls use.
    Fail-open: any error just means the first image pays the load, exactly as
    before. No-op on the mock backend."""
    if settings.vision_backend == "mock":
        return
    model = settings.vision_model_fast or settings.vision_model
    payload = {"model": model, "prompt": "", "stream": False,
               "keep_alive": settings.ollama_keep_alive}
    try:
        t0 = time.monotonic()
        req = urllib.request.Request(settings.ollama_host.rstrip("/") + "/api/generate",
                                     data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=900) as r:   # generous: first load may pull weights
            r.read()
        print(f"[warmup] vision model {model} preloaded in {time.monotonic() - t0:.1f}s", flush=True)
    except Exception as e:
        print(f"[warmup] vision preload skipped ({model}): {e}", flush=True)


def _prep_image(f: Path, work: Path, max_px: int = None):
    """Convert to a capped-size RGB JPEG so the VLM gets a cheap, decodable input
    (also handles webp/heic/gif and huge photos). Returns (path, resize_ms);
    falls back to the original file on any failure. Cached per (file, max_px)."""
    t0 = time.monotonic()
    cap = max_px or settings.decode_max_px
    dst = work / f"{f.stem}.{cap}.vlm.jpg"
    if dst.exists():
        return dst, (time.monotonic() - t0) * 1000
    try:
        from PIL import Image, ImageFile
        ImageFile.LOAD_TRUNCATED_IMAGES = True          # tolerate truncated/partial files
        im = Image.open(f)
        try: im.seek(0)                                 # first frame of animated webp/gif
        except Exception: pass
        try: im.draft("RGB", (cap, cap))                # let the JPEG decoder downscale on load (fast on huge photos)
        except Exception: pass
        im = im.convert("RGB")
        if max(im.size) > cap:
            im.thumbnail((cap, cap))                    # preserves aspect ratio, in place
        im.save(dst, "JPEG", quality=85)
        return dst, (time.monotonic() - t0) * 1000
    except Exception:
        return f, (time.monotonic() - t0) * 1000        # let _vlm try the original


def _keyframes(src: Path, out_dir: Path, n=2):
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(src)],
            capture_output=True, text=True).stdout.strip())
    except Exception:
        dur = 0.0
    frames = []
    for i in range(n):
        t = (dur * (i + 0.5) / n) if dur else i
        fp = out_dir / f"{src.stem}_f{i}.jpg"
        subprocess.run(["ffmpeg", "-y", "-ss", str(t), "-i", str(src), "-frames:v", "1",
                        "-q:v", "3", str(fp)], capture_output=True)
        if fp.exists(): frames.append(fp)
    return frames


# A VLM that balks at an image returns a refusal instead of a caption ("I can't
# describe this…"). We never want that string to leak into the transcript as a
# "caption", so we detect it and fall back to a bare [image] label (see _decode_image).
_REFUSAL_RE = re.compile(
    r"\b(i\s*(can'?t|cannot|won'?t|am unable|'m unable|am not able)|i'?m sorry|"
    r"unable to (process|describe|assist|help)|cannot (assist|help|comply)|"
    r"against my (guidelines|policy)|i (will|must) not)\b", re.I)


def _is_refusal(caption: str) -> bool:
    return bool(caption) and bool(_REFUSAL_RE.search(caption))


def _parse_combined(s: str):
    """Pull CATEGORY / PEOPLE / EXPLICIT / CAPTION out of the single-pass response."""
    cat = re.search(r"CATEGORY\s*=\s*(\w+)", s, re.I)
    ppl = re.search(r"PEOPLE\s*=\s*(yes|no)", s, re.I)
    exp = re.search(r"EXPLICIT\s*=\s*(yes|no)", s, re.I)
    cap = re.search(r"CAPTION\s*=\s*(.+)", s, re.I | re.S)
    category = cat.group(1).lower() if cat else "other"
    people = bool(ppl and ppl.group(1).lower() == "yes")
    explicit = bool(exp and exp.group(1).lower() == "yes")
    caption = cap.group(1).strip() if cap else ""
    if not caption:                                     # model ignored the format
        caption = re.sub(r"CATEGORY.*|PEOPLE.*|EXPLICIT.*", "", s, flags=re.I).strip() or s.strip()
    return category, people, explicit, caption


# --- explicit-image gate: a DEDICATED local NSFW detector (NudeNet), run BEFORE captioning.
# The captioning VLM can NOT be trusted to self-report (it writes the graphic caption but
# answers EXPLICIT=no — proven on real nudes), so the marker hangs on THIS, not the VLM.
# onnxruntime (already a faster-whisper dep) backs NudeNet; the model is pre-baked in the image.
_NUDE = None
_NUDE_TRIED = False


def _nude():
    """Lazy-load the NudeNet detector once. None if it can't load (logged loudly)."""
    global _NUDE, _NUDE_TRIED
    if not _NUDE_TRIED:
        _NUDE_TRIED = True
        try:
            from nudenet import NudeDetector
            _NUDE = NudeDetector()
        except Exception as e:
            print(f"[nsfw] NudeNet unavailable ({e}) — explicit gate degraded "
                  f"(nsfw_required={settings.nsfw_required})", flush=True)
            _NUDE = None
    return _NUDE


def _is_exposed(cls: str) -> bool:
    """True for NudeNet classes meaning actual exposed nudity — not shirtless men, not the
    'COVERED' variants. Substring-matched so minor class-name changes don't silently slip."""
    c = (cls or "").upper()
    if "EXPOSED" not in c or c.startswith("MALE_BREAST"):
        return False
    return any(k in c for k in ("GENITALIA", "ANUS", "BUTTOCK", "BREAST"))


def is_explicit_image(path: Path) -> bool:
    """Does this image contain exposed nudity? Dedicated detector, biased for recall.
    Mock backend → always False (no real pixels). Detector missing / error → respect
    nsfw_required (fail-closed → True so the caption is skipped; fail-open → False)."""
    if settings.vision_backend == "mock":
        return False
    det = _nude()
    if det is None:
        return settings.nsfw_required
    try:
        res = det.detect(str(path))
        return any(_is_exposed(d.get("class", "")) and d.get("score", 0) >= settings.nsfw_threshold
                   for d in res)
    except Exception as e:
        print(f"[nsfw] detect error on {path}: {e}", flush=True)
        return settings.nsfw_required


def _decode_image(f: Path, kind: str, work: Path, model: str = None, max_px: int = None):
    view, resize_ms = (_prep_image(f, work, max_px) if settings.vision_backend != "mock" else (f, 0.0))
    if kind == "sticker":
        cap, ms = _vlm(view, STICKER, num_predict=48, model=model)
        return f.name, {"type": "sticker", "caption": cap,
                        "_t": {"resize_ms": resize_ms, "infer_ms": ms, "calls": 1}}
    # NSFW GATE FIRST (dedicated detector, not the VLM's word): if flagged, carry the
    # neutral marker and SKIP the caption call entirely — the graphic description is never
    # generated. NOTE: adult/legal case only — this does not attempt CSAM.
    if settings.mark_explicit and is_explicit_image(view):
        return f.name, {"type": "image", "explicit": True, "marker": settings.explicit_marker,
                        "tier": "explicit",
                        "_t": {"resize_ms": resize_ms, "infer_ms": 0, "calls": 0}}
    text, ms = _vlm(view, COMBINED, num_predict=settings.vision_num_predict, model=model)
    cat, people, explicit, caption = _parse_combined(text)
    rec = {"type": "image", "category": cat, "people": people}
    if settings.mark_explicit and explicit:
        # VLM self-report as a SECONDARY backup to the detector above (either → marker).
        # The caption is discarded here in CODE, never stored.
        rec.update(explicit=True, marker=settings.explicit_marker, tier="explicit")
    elif _is_refusal(caption):
        # The VLM balked — don't leak its refusal string as a "caption". We can't
        # assert the image is explicit, so we don't mark it; just a bare [image].
        rec["tier"] = "tag"
    elif cat in RICH or people:
        rec["caption"], rec["tier"] = caption, "detailed"
    else:
        rec["tag"], rec["tier"] = caption, "tag"
    rec["_t"] = {"resize_ms": resize_ms, "infer_ms": ms, "calls": 1}
    return f.name, rec


def _caption_of(rec: dict):
    """The single best human-readable line for a decoded item (for a glimpse/receipt).
    Explicit images carry only the neutral marker — never a graphic caption."""
    if rec.get("explicit"):
        return rec.get("marker") or "intimate/explicit image"
    return (rec.get("caption") or rec.get("tag") or rec.get("transcript")
            or "; ".join(rec.get("frame_captions", [])) or None)


def _timing_summary(out: dict, wall_ms: float, model: str, max_px: int):
    """Pop the per-item timing (_t) out of the records and log an aggregate so we
    can see where decode time goes. Records stay clean for media.json."""
    ts = [r.pop("_t") for r in out.values() if "_t" in r]
    if not ts or settings.vision_backend == "mock":
        return
    n = len(ts)
    infer = sum(t["infer_ms"] for t in ts)
    resize = sum(t["resize_ms"] for t in ts)
    calls = sum(t["calls"] for t in ts)
    print(f"[decode/cheap-all] {n} images/stickers | {calls} VLM calls | model={model} | "
          f"wall {wall_ms/1000:.1f}s | infer {infer/1000:.1f}s (avg {infer/n:.0f}ms) | "
          f"resize {resize/1000:.1f}s (avg {resize/n:.0f}ms) | "
          f"workers={settings.decode_workers} max_px={max_px}", flush=True)


def detect_language(text: str):
    """Best-effort dominant language (ISO 639-1, e.g. 'ru') of the chat from its TEXT —
    the strongest, cheapest signal (thousands of messages), and language-GENERAL (no
    audience assumption). Returns a 2-letter code or None → caller falls back to Whisper's
    per-clip auto-detect. Local, no network. Degrades gracefully if langdetect is absent."""
    sample = (text or "").strip()
    if len(sample) < 200:                              # too little text to be confident
        return None
    try:
        from langdetect import detect, DetectorFactory
        DetectorFactory.seed = 0                       # deterministic
        code = detect(sample[:50000])
        return code.split("-")[0] if code else None    # 'zh-cn' -> 'zh'
    except Exception:
        return None


def _transcribe(f: Path, work: Path, wm, language: str = None):
    """Whisper-transcribe the audio of a voice note OR a video message (we strip VIDEO with
    -vn and keep the audio track). Returns (text, quality) — quality carries the per-segment
    confidence Whisper already computes (fuel for the escalation gate), or None if there was
    no usable audio. Quality params: `language` forces the language (skips the flaky per-clip
    auto-detect; None = auto), vad_filter trims silence (cuts hallucination),
    condition_on_previous_text=False stops repetition loops, beam_size from config."""
    wav = work / (f.stem + ".wav")
    subprocess.run(["ffmpeg", "-y", "-i", str(f), "-vn", "-ar", "16000", "-ac", "1", str(wav)],
                   capture_output=True)
    try:
        if not wav.exists() or wav.stat().st_size < 1000:   # no / empty audio track
            return "", None
        seg, info = wm.transcribe(str(wav), language=language or None, beam_size=settings.whisper_beam,
                                  vad_filter=True, condition_on_previous_text=False)
        segs = list(seg)
        text = " ".join(s.text.strip() for s in segs).strip()
        spoken = sum(s.end - s.start for s in segs)
        q = {
            "duration": getattr(info, "duration", 0.0) or spoken,     # full clip length (s)
            "speech": bool(segs),                                     # VAD found speech at all
            "avg_logprob": (sum(s.avg_logprob * (s.end - s.start) for s in segs) / spoken
                            if spoken > 0 else None),                  # duration-weighted confidence
            "compression_ratio": max((s.compression_ratio for s in segs), default=None),
        }
        return text, q
    finally:
        wav.unlink(missing_ok=True)


def _suspect_transcript(text: str, q, language: str):
    """Gate for the tiered-ASR escalation: does this pass-1 transcript look like garbage?
    Returns (severity, reason) — lower severity = worse, so suspects sort worst-first —
    or None if the transcript looks fine. Signals, most certain first: VAD found speech
    but no text came out; the output is in a different language than the one we forced
    (mixed-language chats); a repetition loop (compression_ratio, OpenAI's own failure
    threshold); low decoder confidence (duration-weighted avg_logprob)."""
    if not q:
        return None
    if not text:
        return (-10.0, "empty") if q["speech"] else None
    lp = q["avg_logprob"]
    if language and len(text) >= 60:
        try:
            from langdetect import detect, DetectorFactory
            DetectorFactory.seed = 0
            if detect(text[:2000]).split("-")[0] != language:
                return (min(lp if lp is not None else 0.0, -3.0), "lang")
        except Exception:
            pass
    cr = q["compression_ratio"]
    if cr is not None and cr > 2.4:
        return (min(lp if lp is not None else 0.0, -2.0), "loop")
    if lp is not None and lp < settings.whisper_escalate_logprob:
        return (lp, "logprob")
    return None


def decode_images(files, work: Path, deep: bool = False, on_item=None, deadline=None) -> dict:
    """Caption images/stickers (and, cheap pass only, video KEYFRAMES) on the VLM.

    deep=False — the cheap-all pass: small px, one COMBINED classify+caption call.
    deep=True  — the read REQUESTED these: full px, DEEP_GUARDED rich caption.
    Both paths run the NudeNet gate FIRST (marker instead of caption; the graphic
    description is never generated). `on_item(name, rec, done, total)` fires per
    file; `deadline` (a time.monotonic() stamp) bounds the batch — whatever isn't
    done by then is marked skipped, never stalls the pipeline."""
    import concurrent.futures as _cf
    work.mkdir(parents=True, exist_ok=True)
    files = list(files)
    out = {}
    if not files:
        return out
    wall0 = time.monotonic()
    model = settings.vision_model_fast or settings.vision_model
    px = settings.decode_max_px if deep else settings.decode_max_px_fast

    def one(f):
        k = file_type(f)
        if k == "video":                          # visual keyframes (cheap pass only)
            rec = {"type": "video"}
            try:
                caps = []
                for fr in _keyframes(f, work):
                    view, _ = _prep_image(fr, work, px)
                    c, _ = _vlm(view, FRAME, num_predict=48, model=model)
                    caps.append(c)
                if caps:
                    rec["frame_captions"] = caps
            except Exception as e:
                rec["error"] = str(e)
            return f.name, rec
        if not deep:
            return _decode_image(f, "sticker" if k == "sticker" else "image", work, model, px)
        # deep: a richer look at what the read asked to see
        view, _ = (_prep_image(f, work, px) if settings.vision_backend != "mock" else (f, 0.0))
        if k == "sticker":
            cap, _ = _vlm(view, STICKER, num_predict=48, model=model)
            return f.name, {"type": "sticker", "caption": cap, "tier": "deep"}
        if settings.mark_explicit and is_explicit_image(view):
            return f.name, {"type": "image", "explicit": True,
                            "marker": settings.explicit_marker, "tier": "explicit"}
        text, _ = _vlm(view, DEEP_GUARDED, num_predict=settings.vision_num_predict, model=model)
        _, _, explicit, caption = _parse_combined(text)
        if settings.mark_explicit and explicit:        # VLM self-report as a secondary backup
            return f.name, {"type": "image", "explicit": True,
                            "marker": settings.explicit_marker, "tier": "explicit"}
        if _is_refusal(caption):
            return f.name, {"type": "image", "tier": "tag"}   # never leak a refusal string
        return f.name, {"type": "image", "caption": caption, "tier": "deep"}

    done = 0

    def step(name, rec):
        nonlocal done
        done += 1
        out[name] = rec
        if on_item:
            on_item(name, rec, done, len(files))

    ex = ThreadPoolExecutor(max_workers=settings.decode_workers)
    futs = {ex.submit(one, f): f for f in files}
    budget = (deadline - time.monotonic()) if deadline else None
    try:
        for fut in as_completed(futs, timeout=max(5, budget) if budget is not None else None):
            f = futs[fut]
            try:
                name, rec = fut.result()
            except Exception as e:
                name, rec = f.name, {"type": file_type(f), "error": str(e)}
            step(name, rec)
    except _cf.TimeoutError:
        for fut, f in futs.items():                 # out of time → skip, don't stall
            if f.name not in out:
                step(f.name, {"type": file_type(f), "error": "decode budget exhausted (skipped)"})
                print(f"[decode/images] {f.name} | SKIPPED (batch deadline)", flush=True)
    ex.shutdown(wait=False)
    _timing_summary(out, (time.monotonic() - wall0) * 1000, model, px)
    return out


def decode_speech(files, work: Path, language: str = None, on_item=None,
                  on_reinspect=None, deadline=None) -> dict:
    """Whisper-transcribe voice notes + video speech. ONE model load for the whole
    batch (the aborted audio-on-demand branch died on per-batch reloads). Video:
    round notes always; shared clips only under video_max_mb. Tiered-ASR
    escalation runs at batch end (worst-first, capped) unless the deadline has
    passed. `on_item(name, rec, done, total)` per clip; escalated clips re-emit
    both via on_item (evidence) and on_reinspect (the UI's second-look counter)."""
    work.mkdir(parents=True, exist_ok=True)
    files = list(files)
    out = {}
    if not files:
        return out
    done = 0

    def step(name, rec):
        nonlocal done
        done += 1
        out[name] = rec
        if on_item:
            on_item(name, rec, done, len(files))

    if settings.vision_backend == "mock":
        for f in files:
            if file_type(f) == "audio":
                step(f.name, {"type": "audio", "transcript": f"[mock transcript {f.stem[-6:]}]"})
            elif settings.transcribe_video:
                step(f.name, {"type": "video", "transcript": f"[mock video speech {f.stem[-6:]}]"})
            else:
                step(f.name, {"type": "video"})
        return out

    audio_files = [f for f in files if file_type(f) == "audio"]
    video_files = [f for f in files if file_type(f) == "video"]
    wm = None
    if audio_files or (video_files and settings.transcribe_video):
        try:
            from faster_whisper import WhisperModel
            wm = WhisperModel(settings.whisper_model, device="cpu", compute_type="int8")
        except Exception as e:
            for f in files:
                step(f.name, {"type": file_type(f), "error": f"speech decode unavailable: {e}"})
            return out

    suspects = []                                    # (severity, reason, duration_s, file, rec)
    for f in audio_files:
        if deadline and time.monotonic() > deadline:
            step(f.name, {"type": "audio", "error": "decode budget exhausted (skipped)"}); continue
        try:
            tx, q = _transcribe(f, work, wm, language)
            rec = {"type": "audio", "transcript": tx}
            flag = _suspect_transcript(tx, q, language)
            if flag:
                suspects.append((flag[0], flag[1], q["duration"], f, rec))
        except Exception as e:
            rec = {"type": "audio", "error": str(e)}
        step(f.name, rec)

    for f in video_files:
        rec = {"type": "video"}
        if settings.transcribe_video and wm is not None and \
                (is_video_note(f) or f.stat().st_size <= settings.video_max_mb * 1_000_000):
            if deadline and time.monotonic() > deadline:
                rec["error"] = "decode budget exhausted (skipped)"
            else:
                try:
                    tx, q = _transcribe(f, work, wm, language)
                    if tx:
                        rec["transcript"] = tx
                    flag = _suspect_transcript(tx, q, language)
                    if flag:
                        suspects.append((flag[0], flag[1], q["duration"], f, rec))
                except Exception as e:
                    rec["error"] = str(e)
        if rec.get("video_note") is None and is_video_note(f):
            rec["video_note"] = True
        step(f.name, rec)

    # Tiered ASR escalation: re-run the clips whose pass-1 transcript scored as garbage on
    # the bigger model — worst-first, under a cap on total re-run audio. The small model is
    # freed before the big one loads, so peak RAM stays ≈ one model. Fail-open: any failure
    # keeps the pass-1 transcripts and logs loudly — never kills the decode.
    esc = settings.whisper_escalate_model.strip()
    if suspects and esc and esc != settings.whisper_model and wm is not None \
            and not (deadline and time.monotonic() > deadline):
        try:
            suspects.sort(key=lambda s: s[0])
            cap = settings.whisper_escalate_max_s
            budget = float(cap) if cap > 0 else float("inf")
            chosen = []
            for sev, reason, dur, f, rec in suspects:
                if dur <= budget:
                    chosen.append((f, rec, reason))
                    budget -= dur
            if chosen:
                t0 = time.monotonic()
                # Announce the phase BEFORE the big model loads (slow on CPU) so the
                # UI switches to its own "re-checking" counter immediately.
                if on_reinspect:
                    on_reinspect(0, len(chosen), None)
                from faster_whisper import WhisperModel
                del wm; gc.collect()
                wm = WhisperModel(esc, device="cpu", compute_type="int8")
                fixed = 0
                for i, (f, rec, reason) in enumerate(chosen):
                    try:
                        tx2, _ = _transcribe(f, work, wm, language)
                        if tx2 and tx2 != rec.get("transcript"):
                            rec["transcript"] = tx2
                            fixed += 1
                        rec["asr"] = {"escalated": esc, "reason": reason}
                        if on_item:                    # corrected transcript = fresh evidence
                            on_item(f.name, rec, len(files), len(files))
                    except Exception:
                        pass                           # keep the pass-1 text for this clip
                    if on_reinspect:
                        on_reinspect(i + 1, len(chosen), {"file": f.name, "type": rec.get("type"),
                                                          "caption": _caption_of(rec), "reinspected": True})
                print(f"[whisper] escalation: {len(chosen)}/{len(suspects)} flagged clips re-run "
                      f"on {esc} in {time.monotonic() - t0:.0f}s | {fixed} transcripts replaced | "
                      f"cap {cap if cap > 0 else 'none'}s", flush=True)
        except Exception as e:
            print(f"[whisper] escalation FAILED (keeping pass-1 transcripts): {e}", flush=True)

    return out
