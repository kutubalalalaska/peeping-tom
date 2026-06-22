"""Local media decoder — runs entirely on the machine (privacy boundary).

  audio   -> Whisper transcription
  image   -> ONE VLM pass: classify (+people flag) and caption in a single call,
             caption detail scales with content (people/scene = full, else terse)
  sticker -> short interpretive caption
  video   -> keyframe captions (+ audio transcript)

Speed levers (the image-encode is the cost on a single GPU):
  - every image is resized/converted to a capped-size RGB JPEG before the VLM
    (DECODE_MAX_PX), so the model isn't fed a 12 MP original;
  - triage + caption are a SINGLE call (no redundant image-encode);
  - the model is kept warm (keep_alive) and generation is bounded (num_predict,
    temperature 0).
The captioner stays BLIND — it never sees surrounding messages.

Vision goes to the bundled Ollama service; audio uses faster-whisper. Images run
through a thread pool. Decode reports per-item progress (done/total + the item it
just finished) so the UI can show live glimpses and a completion percentage, and
prints a timing summary so we can see where the time goes.
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
COMBINED = (
    "Describe this single chat image. Output exactly these two lines and nothing else:\n"
    "CATEGORY=<people|scene|screenshot|document|diagram|meme|other>; PEOPLE=<yes|no>\n"
    "CAPTION=<text>\n"
    "Rules: PEOPLE=yes only if real human faces are visible. If CATEGORY is people or "
    "scene, CAPTION is 1-2 precise lines (what/who is shown, setting, visible text, mood); "
    "otherwise CAPTION is at most 8 words. Only what is visible — never guess identities."
)
STICKER = "WhatsApp sticker. One short line: what it depicts and the emotion it conveys (stickers stand in for words)."
FRAME = "Frame from a short video in a chat. One line: what it shows."
RICH = {"people", "scene"}


def file_type(p: Path) -> str:
    n, e = p.name.upper(), p.suffix.lower()
    if "AUDIO" in n or e in (".opus", ".m4a", ".mp3", ".wav", ".ogg"): return "audio"
    if "VIDEO" in n or "GIF" in n or e in (".mp4", ".mov", ".3gp"): return "video"
    if e == ".webp" or "STICKER" in n: return "sticker"
    if "PHOTO" in n or "IMAGE" in n or e in (".jpg", ".jpeg", ".png", ".heic", ".heif"): return "image"
    return "document"


def _vlm(img: Path, prompt: str, num_predict: int = None):
    """Run one vision call. Returns (text, elapsed_ms). The image-encode dominates,
    so callers should pass an already-resized image and avoid extra passes."""
    t0 = time.monotonic()
    if settings.vision_backend == "mock":
        import hashlib
        h = int(hashlib.md5(img.name.encode()).hexdigest(), 16)
        if "CATEGORY=" in prompt:                      # combined classify+caption
            people = "yes" if ("PHOTO" in img.name.upper() and h % 2 == 0) else "no"
            cat = "people" if people == "yes" else ("screenshot" if h % 3 else "document")
            cap = f"[mock caption {img.stem[-6:]}]" if (cat in RICH or people == "yes") else f"mock {cat}"
            return f"CATEGORY={cat}; PEOPLE={people}\nCAPTION={cap}", (time.monotonic() - t0) * 1000
        return f"[mock caption {img.stem[-6:]}]", (time.monotonic() - t0) * 1000

    b64 = base64.b64encode(img.read_bytes()).decode()
    options = {"temperature": 0}
    if num_predict:
        options["num_predict"] = num_predict
    payload = {"model": settings.vision_model, "prompt": prompt, "images": [b64],
               "stream": False, "keep_alive": settings.ollama_keep_alive, "options": options}
    req = urllib.request.Request(settings.ollama_host.rstrip("/") + "/api/generate",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        text = json.loads(r.read())["response"].strip()
    return text, (time.monotonic() - t0) * 1000


def _prep_image(f: Path, work: Path):
    """Convert to a capped-size RGB JPEG so the VLM gets a cheap, decodable input
    (also handles webp/heic/gif and huge photos). Returns (path, resize_ms);
    falls back to the original file on any failure. Cached in work/."""
    t0 = time.monotonic()
    dst = work / (f.stem + ".vlm.jpg")
    if dst.exists():
        return dst, (time.monotonic() - t0) * 1000
    try:
        from PIL import Image
        im = Image.open(f)
        try: im.seek(0)                                 # first frame of animated webp/gif
        except Exception: pass
        im = im.convert("RGB")
        cap = settings.decode_max_px
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


def _parse_combined(s: str):
    """Pull CATEGORY / PEOPLE / CAPTION out of the single-pass response."""
    cat = re.search(r"CATEGORY\s*=\s*(\w+)", s, re.I)
    ppl = re.search(r"PEOPLE\s*=\s*(yes|no)", s, re.I)
    cap = re.search(r"CAPTION\s*=\s*(.+)", s, re.I | re.S)
    category = cat.group(1).lower() if cat else "other"
    people = bool(ppl and ppl.group(1).lower() == "yes")
    caption = cap.group(1).strip() if cap else ""
    if not caption:                                     # model ignored the format
        caption = re.sub(r"CATEGORY.*|PEOPLE.*", "", s, flags=re.I).strip() or s.strip()
    return category, people, caption


def _decode_image(f: Path, kind: str, work: Path):
    view, resize_ms = (_prep_image(f, work) if settings.vision_backend != "mock" else (f, 0.0))
    if kind == "sticker":
        cap, ms = _vlm(view, STICKER, num_predict=48)
        return f.name, {"type": "sticker", "caption": cap,
                        "_t": {"resize_ms": resize_ms, "infer_ms": ms, "calls": 1}}
    text, ms = _vlm(view, COMBINED, num_predict=settings.vision_num_predict)
    cat, people, caption = _parse_combined(text)
    rec = {"type": "image", "category": cat, "people": people}
    if cat in RICH or people:
        rec["caption"], rec["tier"] = caption, "detailed"
    else:
        rec["tag"], rec["tier"] = caption, "tag"
    rec["_t"] = {"resize_ms": resize_ms, "infer_ms": ms, "calls": 1}
    return f.name, rec


def _caption_of(rec: dict):
    """The single best human-readable line for a decoded item (for a glimpse)."""
    return (rec.get("caption") or rec.get("tag") or rec.get("transcript")
            or "; ".join(rec.get("frame_captions", [])) or None)


def _timing_summary(out: dict, wall_ms: float):
    """Pop the per-item timing (_t) out of the records and log an aggregate so we
    can see where decode time goes. Records stay clean for media.json."""
    ts = [r.pop("_t") for r in out.values() if "_t" in r]
    if not ts or settings.vision_backend == "mock":
        return
    n = len(ts)
    infer = sum(t["infer_ms"] for t in ts)
    resize = sum(t["resize_ms"] for t in ts)
    calls = sum(t["calls"] for t in ts)
    print(f"[decode] {n} images/stickers | {calls} VLM calls | wall {wall_ms/1000:.1f}s | "
          f"infer {infer/1000:.1f}s (avg {infer/n:.0f}ms) | "
          f"resize {resize/1000:.1f}s (avg {resize/n:.0f}ms) | "
          f"workers={settings.decode_workers} max_px={settings.decode_max_px}", flush=True)


def decode_media(files, work: Path, on_progress=None) -> dict:
    """files: list[Path]. Returns {filename: record}. After each decoded item,
    calls on_progress(done, total, item) where item is {file, type, caption|None}.
    Caches nothing here — the caller persists media.json."""
    work.mkdir(parents=True, exist_ok=True)
    wall0 = time.monotonic()
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

    # images + stickers, parallel
    with ThreadPoolExecutor(max_workers=settings.decode_workers) as ex:
        futs = {ex.submit(_decode_image, f, k, work): (f, k) for f, k in img_jobs}
        for fut in as_completed(futs):
            f, k = futs[fut]
            try:
                name, rec = fut.result()
            except Exception as e:
                name, rec = f.name, {"type": "sticker" if k == "sticker" else "image", "error": str(e)}
            step(name, rec)

    # audio
    if by.get("audio"):
        if settings.vision_backend == "mock":
            for f in by["audio"]:
                step(f.name, {"type": "audio", "transcript": f"[mock transcript {f.stem[-6:]}]"})
        else:
            try:
                from faster_whisper import WhisperModel
                wm = WhisperModel(settings.whisper_model, device="cpu", compute_type="int8")
            except Exception as e:
                wm = None
                for f in by["audio"]:
                    step(f.name, {"type": "audio", "error": f"voice-note decode unavailable: {e}"})
            if wm:
                for f in by["audio"]:
                    try:
                        wav = work / (f.stem + ".wav")
                        subprocess.run(["ffmpeg", "-y", "-i", str(f), "-ar", "16000", "-ac", "1", str(wav)],
                                       capture_output=True)
                        seg, _ = wm.transcribe(str(wav), beam_size=1)
                        rec = {"type": "audio", "transcript": " ".join(s.text.strip() for s in seg).strip()}
                        wav.unlink(missing_ok=True)
                    except Exception as e:
                        rec = {"type": "audio", "error": str(e)}
                    step(f.name, rec)

    # video
    for f in by.get("video", []):
        rec = {"type": "video"}
        frames = _keyframes(f, work)
        try:
            caps = []
            for fr in frames:
                view, _ = (_prep_image(fr, work) if settings.vision_backend != "mock" else (fr, 0.0))
                c, _ = _vlm(view, FRAME, num_predict=48)
                caps.append(c)
            rec["frame_captions"] = caps
        except Exception as e:
            rec["error"] = str(e)
        step(f.name, rec)

    for f in by.get("document", []):
        out.setdefault(f.name, {"type": "document"})

    _timing_summary(out, (time.monotonic() - wall0) * 1000)
    return out
