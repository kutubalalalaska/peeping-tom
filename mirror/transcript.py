"""Assemble parsed messages + decoded media into the text transcript that the
read consumes. Each line is prefixed with its #id so the read can cite [#id].

Only this text crosses the privacy boundary — never the raw media.
"""

import os


def _attach_label(fname: str, media: dict) -> str:
    rec = media.get(fname) or media.get(os.path.basename(fname)) or {}
    u = fname.upper()
    if "AUDIO" in u or u.endswith((".OPUS", ".M4A", ".MP3", ".OGG", ".WAV")):
        t = rec.get("transcript")
        return f'[voice message: "{t}"]' if t else "[voice message]"
    if u.endswith((".WEBP", ".TGS")) or "STICKER" in u:
        c = rec.get("caption")
        return f"[sticker: {c}]" if c else "[sticker]"
    if "VIDEO" in u or "GIF" in u or u.endswith((".MP4", ".MOV", ".WEBM")):
        c = rec.get("caption") or "; ".join(rec.get("frame_captions", []))
        if rec.get("transcript"):
            c = (c + " | said: " + rec["transcript"]) if c else "said: " + rec["transcript"]
        return f"[video: {c}]" if c else "[video]"
    if "PHOTO" in u or "IMAGE" in u or u.endswith((".JPG", ".JPEG", ".PNG", ".HEIC")):
        c = rec.get("caption") or rec.get("tag")
        who = rec.get("people_named")
        label = f"image of {', '.join(who)}" if who else "image"
        return f"[{label}: {c}]" if c else f"[{label}]"
    return f"[document: {os.path.basename(fname)}]"


def assemble(messages, media: dict) -> str:
    """Return the full media-rich transcript, one message per line, with #ids."""
    lines = []
    for m in messages:
        body = m.text
        if m.media:
            labels = " ".join(_attach_label(f, media) for f in m.media)
            body = (body + " " + labels).strip() if body else labels
        lines.append(f"#{m.id} [{m.ts}] {m.sender}: {body}")
    return "\n".join(lines)


def stats(messages, media: dict) -> dict:
    from collections import Counter
    senders = Counter(m.sender for m in messages)
    n_media = sum(len(m.media) for m in messages)
    n_decoded = sum(1 for v in media.values() if v.get("caption") or v.get("transcript") or v.get("tag"))
    return {
        "messages": len(messages),
        "date_range": [messages[0].ts, messages[-1].ts] if messages else [],
        "senders": dict(senders),
        "media_attached": n_media,
        "media_decoded": n_decoded,
    }
