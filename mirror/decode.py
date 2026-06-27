"""Local media decoder — runs entirely on the machine (privacy boundary).

Two-pass design (cost follows value):
  - decode_media  = the CHEAP-ALL pass: every image/sticker/video gets a caption
                    from a small VLM (VISION_MODEL_FAST) at a small resolution.
                    Audio -> Whisper. This is what the first read sees.
  - decode_deep   = the DEEP pass: re-caption only the images the FRONTIER read
                    selected, on the big VLM (VISION_MODEL) at full resolution.
                    Often 0 images — the read escalates only when it would matter.

Speed levers: the image *encode* is the cost, so we (a) resize before the VLM,
(b) do ONE call per image (classify + caption together), (c) use a small model
for the all-images pass and reserve the big model for the picked few, (d) keep
the model warm and bound generation. The captioner stays BLIND — it never sees
surrounding messages; the frontier selecting which images to deepen does not feed
context into the caption, so each caption is still independent evidence.

Vision goes to the bundled Ollama service; audio uses faster-whisper. Images run
through a thread pool; decode reports per-item progress and prints a timing line.
"""

import base64, json, re, subprocess, time, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .config import settings

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


def file_type(p: Path) -> str:
    n, e = p.name.upper(), p.suffix.lower()
    if "AUDIO" in n or e in (".opus", ".m4a", ".mp3", ".wav", ".ogg"): return "audio"
    if "VIDEO" in n or "GIF" in n or e in (".mp4", ".mov", ".3gp", ".webm", ".gif"): return "video"
    # .tgs = Telegram's gzipped-Lottie animated sticker (no raster the VLM can read);
    # it's captioned from the message emoji upstream and skipped here.
    if e in (".webp", ".tgs") or "STICKER" in n: return "sticker"
    if "PHOTO" in n or "IMAGE" in n or e in (".jpg", ".jpeg", ".png", ".heic", ".heif"): return "image"
    return "document"


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


def _transcribe(f: Path, work: Path, wm, language: str = None) -> str:
    """Whisper-transcribe the audio of a voice note OR a video message (we strip VIDEO with
    -vn and keep the audio track). Returns "" if there's no usable audio. Quality params:
    `language` forces the language (skips the flaky per-clip auto-detect; None = auto),
    vad_filter trims silence (cuts hallucination), condition_on_previous_text=False stops
    repetition loops, beam_size from config."""
    wav = work / (f.stem + ".wav")
    subprocess.run(["ffmpeg", "-y", "-i", str(f), "-vn", "-ar", "16000", "-ac", "1", str(wav)],
                   capture_output=True)
    try:
        if not wav.exists() or wav.stat().st_size < 1000:   # no / empty audio track
            return ""
        seg, _ = wm.transcribe(str(wav), language=language or None, beam_size=settings.whisper_beam,
                               vad_filter=True, condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in seg).strip()
    finally:
        wav.unlink(missing_ok=True)


def _is_video_note(f: Path) -> bool:
    """A round video MESSAGE (Telegram stores these under round_video_messages/), as
    opposed to a shared video clip — always worth transcribing, regardless of size."""
    return "round_video" in str(f).lower() or "video_message" in str(f).lower()


def decode_media(files, work: Path, on_progress=None, language: str = None) -> dict:
    """CHEAP-ALL pass. files: list[Path]. Returns {filename: record}. After each
    decoded item calls on_progress(done, total, item={file,type,caption|None}).
    Images/stickers/video use the small VISION_MODEL_FAST at decode_max_px_fast.
    Voice notes AND video messages are Whisper-transcribed (their speech is data);
    video keyframes (the visual) are captioned only in cheap-all mode, not at scale.
    `language` (ISO code or None) is passed to Whisper for all speech (None = auto)."""
    work.mkdir(parents=True, exist_ok=True)
    wall0 = time.monotonic()
    fast_model = settings.vision_model_fast or settings.vision_model
    fast_px = settings.decode_max_px_fast
    by = {}
    for f in files:
        by.setdefault(file_type(f), []).append(f)
    out = {}

    img_jobs = [(f, "image") for f in by.get("image", [])] + [(f, "sticker") for f in by.get("sticker", [])]
    total = len(img_jobs) + len(by.get("audio", [])) + len(by.get("video", []))
    done = 0

    def step(name: str, rec: dict):
        nonlocal done
        done += 1
        out[name] = rec
        if on_progress:
            on_progress(done, total, {"file": name, "type": rec.get("type", "image"),
                                      "caption": _caption_of(rec)})

    # images + stickers, parallel, on the small model
    with ThreadPoolExecutor(max_workers=settings.decode_workers) as ex:
        futs = {ex.submit(_decode_image, f, k, work, fast_model, fast_px): (f, k) for f, k in img_jobs}
        for fut in as_completed(futs):
            f, k = futs[fut]
            try:
                name, rec = fut.result()
            except Exception as e:
                name, rec = f.name, {"type": "sticker" if k == "sticker" else "image", "error": str(e)}
            step(name, rec)

    # speech: voice notes + video messages share one Whisper load.
    audio_files = list(by.get("audio", []))
    video_files = list(by.get("video", []))
    if settings.vision_backend == "mock":
        for f in audio_files:
            step(f.name, {"type": "audio", "transcript": f"[mock transcript {f.stem[-6:]}]"})
    else:
        wm = None
        if audio_files or (video_files and settings.transcribe_video):
            try:
                from faster_whisper import WhisperModel
                wm = WhisperModel(settings.whisper_model, device="cpu", compute_type="int8")
            except Exception as e:
                for f in audio_files:
                    step(f.name, {"type": "audio", "error": f"voice-note decode unavailable: {e}"})
        for f in audio_files:
            if wm is None:
                break                                # already error-stepped above
            try:
                rec = {"type": "audio", "transcript": _transcribe(f, work, wm, language)}
            except Exception as e:
                rec = {"type": "audio", "error": str(e)}
            step(f.name, rec)

    # video: transcribe the SPEECH (talking video messages), + keyframe the visual only in
    # cheap-all mode (skipped at scale — iterative mode leaves frames to the deep pass).
    for f in video_files:
        rec = {"type": "video"}
        if settings.vision_backend == "mock":
            if settings.transcribe_video:
                rec["transcript"] = f"[mock video speech {f.stem[-6:]}]"
        else:
            if settings.transcribe_video and wm is not None:
                if _is_video_note(f) or f.stat().st_size <= settings.video_max_mb * 1_000_000:
                    try:
                        tx = _transcribe(f, work, wm, language)
                        if tx:
                            rec["transcript"] = tx
                    except Exception as e:
                        rec["error"] = str(e)
            if not settings.iterative_discovery:     # visual frames only for small (cheap-all) chats
                try:
                    caps = []
                    for fr in _keyframes(f, work):
                        view, _ = _prep_image(fr, work, fast_px)
                        c, _ = _vlm(view, FRAME, num_predict=48, model=fast_model)
                        caps.append(c)
                    if caps:
                        rec["frame_captions"] = caps
                except Exception as e:
                    rec.setdefault("error", str(e))
        step(f.name, rec)

    for f in by.get("document", []):
        out.setdefault(f.name, {"type": "document"})

    _timing_summary(out, (time.monotonic() - wall0) * 1000, fast_model, fast_px)
    return out


def decode_deep(files, work: Path, on_progress=None) -> dict:
    """DEEP pass. Re-caption the frontier-SELECTED images on the small/fast model
    (the 3B) at full resolution with the DETAILED prompt — a richer look at the few
    images the read asked to see (no big-model tier; the 3B is enough, per the
    baseline). Returns {filename: {caption, tier:'deep'}} to merge into media.json.
    Runs in parallel and is bounded: a hung/bad image is skipped, never stalls the batch.

    SELF-GUARDED: the deep pass classifies EXPLICIT itself (DEEP_GUARDED prompt) and
    drops an explicit image to the neutral marker in code — so nothing graphic crosses
    even in iterative-discovery mode, where no prior cheap-all flag exists. The server
    also skips already-flagged explicit files in _image_files_for_ids (defense in depth)."""
    import concurrent.futures as _cf
    work.mkdir(parents=True, exist_ok=True)
    model = settings.vision_model_fast or settings.vision_model
    out, files = {}, list(files)
    if not files:
        return out

    def one(f):
        view, _ = (_prep_image(f, work, settings.decode_max_px)
                   if settings.vision_backend != "mock" else (f, 0.0))
        # NSFW GATE FIRST (dedicated detector): if flagged, marker + SKIP the caption call
        # — the graphic description is never generated. This is the real guard the deep
        # path needs (the VLM's own EXPLICIT self-report misses real nudes).
        if settings.mark_explicit and is_explicit_image(view):
            return {"explicit": True, "marker": settings.explicit_marker, "tier": "explicit", "_ms": 0}
        text, ms = _vlm(view, DEEP_GUARDED, num_predict=settings.vision_num_predict, model=model)
        _, _, explicit, caption = _parse_combined(text)
        if settings.mark_explicit and explicit:        # VLM self-report as a secondary backup
            return {"explicit": True, "marker": settings.explicit_marker, "tier": "explicit", "_ms": ms}
        if _is_refusal(caption):
            return {"tier": "tag", "_ms": ms}        # don't leak a refusal string as a caption
        return {"caption": caption, "tier": "deep", "_ms": ms}

    ex = ThreadPoolExecutor(max_workers=settings.decode_workers)
    futs = {ex.submit(one, f): f for f in files}
    budget = settings.vision_timeout * 2 + 30           # overall wall budget for the (parallel) batch
    done = 0
    try:
        for fut in as_completed(futs, timeout=budget):
            f = futs[fut]; done += 1
            try:
                rec = fut.result(); ms = rec.pop("_ms", 0); out[f.name] = rec
                if settings.vision_backend != "mock":
                    print(f"[decode/deep] {f.name} | {ms/1000:.1f}s", flush=True)
            except Exception as e:
                out[f.name] = {"error": str(e)}
                if settings.vision_backend != "mock":
                    print(f"[decode/deep] {f.name} | ERROR: {e}", flush=True)
            if on_progress:
                on_progress(done, len(files), {"file": f.name, "type": "image", "caption": out[f.name].get("caption")})
    except _cf.TimeoutError:
        for fut, f in futs.items():                     # whatever didn't finish in time → skip, don't stall
            if f.name not in out:
                out[f.name] = {"error": "deep timeout (skipped)"}
                print(f"[decode/deep] {f.name} | SKIPPED (batch timeout)", flush=True)
    ex.shutdown(wait=False)
    return out
