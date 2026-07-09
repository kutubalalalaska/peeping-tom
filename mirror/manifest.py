"""Media METADATA probe — the manifest that lets the read reason about media it
has NOT decoded. Durations via ffprobe, image dimensions via a PIL header read,
sizes from the filesystem: seconds of work, no model loads, nothing leaves the
machine. Fast mode's whole premise: the model sees `[voice 3:47 — undecoded]`
and a corpus-level summary, and DECIDES what is worth decoding.
"""

import subprocess
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from . import mediatypes


def _seconds(path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=20).stdout.strip()
        return round(float(out), 1)
    except Exception:
        return 0.0


def _dims(path):
    try:
        from PIL import Image
        with Image.open(path) as im:        # header-only read; pixels stay on disk
            return im.size
    except Exception:
        return None


def _probe(f):
    k = mediatypes.kind(f)
    rec = {"type": k, "bytes": f.stat().st_size}
    if k in ("audio", "video"):
        s = _seconds(f)
        if s:
            rec["seconds"] = s
        if k == "video" and mediatypes.is_video_note(f):
            rec["video_note"] = True
    elif k in ("image", "sticker") and f.suffix.lower() != ".tgs":
        wh = _dims(f)
        if wh:
            rec["w"], rec["h"] = wh
    return f.name, rec


def build(files, workers: int = 8) -> dict:
    """{filename: {type, bytes, seconds?, w?, h?, video_note?}} for every media
    file. Parallel; a file that fails to probe still gets a bare record."""
    out = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for name, rec in ex.map(_probe, files):
            out[name] = rec
    return out


def _mss(s: float) -> str:
    s = int(round(s))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def header(msgs, media: dict) -> str:
    """The `MEDIA MANIFEST:` line prepended to a placeholder transcript — the
    corpus-level aggregates the read reasons over (counts, audio volume, who
    leans on which channel)."""
    undecoded = {n: r for n, r in media.items()
                 if not (r.get("transcript") or r.get("caption") or r.get("tag") or r.get("explicit"))}
    if not undecoded:
        return ""
    file_sender = {}
    file_ts = {}
    for m in msgs:
        for f in m.media or []:
            file_sender.setdefault(f, m.sender)
            file_ts.setdefault(f, m.ts)
    parts = []
    kinds = Counter(r["type"] for r in undecoded.values())

    def _by_sender(kind):
        c = Counter(file_sender.get(n, "?") for n, r in undecoded.items() if r["type"] == kind)
        return ", ".join(f"{s} {k}" for s, k in c.most_common(2))

    if kinds.get("image"):
        parts.append(f"{kinds['image']} images ({_by_sender('image')})")
    aud = [(n, r) for n, r in undecoded.items() if r["type"] == "audio"]
    if aud:
        total = sum(r.get("seconds") or 0 for _, r in aud)
        top = max(aud, key=lambda nr: nr[1].get("seconds") or 0)
        longest = ""
        if top[1].get("seconds"):
            ts = (file_ts.get(top[0]) or "")[:10]
            longest = f", longest {_mss(top[1]['seconds'])}" + (f" on {ts}" if ts else "")
        parts.append(f"{len(aud)} voice notes (total {_mss(total)}{longest}; {_by_sender('audio')})")
    vid = [(n, r) for n, r in undecoded.items() if r["type"] == "video"]
    if vid:
        notes = sum(1 for _, r in vid if r.get("video_note"))
        total = sum(r.get("seconds") or 0 for _, r in vid)
        lab = f"{len(vid)} videos" + (f" ({notes} video notes)" if notes else "")
        parts.append(f"{lab} (total {_mss(total)})")
    if kinds.get("sticker"):
        parts.append(f"{kinds['sticker']} stickers ({_by_sender('sticker')})")
    if not parts:
        return ""
    return (f"MEDIA MANIFEST: {len(undecoded)} media items are still undecoded — "
            + "; ".join(parts)
            + ". Their content can be requested (instructions at the end).")
