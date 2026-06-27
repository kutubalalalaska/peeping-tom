"""Assemble parsed messages + decoded media into the text transcript that the
read consumes. Each line is prefixed with its #id so the read can cite [#id].

Only this text crosses the privacy boundary — never the raw media.

Two assemblers, one shape of evidence:
  - assemble          = the full, human-readable form (date+sender on every line).
  - assemble_compact  = a LOSSLESS token-lean form (SCALING.md Stage 2): senders map
                        to short tokens (legend up top), the date prints once per day,
                        lines carry only HH:MM. ~30-40% fewer tokens, #ids preserved so
                        citations still resolve. Used when COMPACT_TRANSCRIPT is on (or,
                        later, auto-enabled by the size gate for big corpora).
"""

import os

from .config import settings


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


def _sender_token(i: int) -> str:
    """0->A, 1->B, … 25->Z, 26->AA … (bijective base-26). Stable, short sender tags."""
    s, i = "", i + 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _body_of(m, media: dict) -> str:
    body = m.text
    if m.media:
        labels = " ".join(_attach_label(f, media) for f in m.media)
        body = (body + " " + labels).strip() if body else labels
    return body


def assemble_compact(messages, media: dict):
    """LOSSLESS token-lean transcript (SCALING.md Stage 2). Returns (text, legend):
    senders mapped to short tokens (legend in a FORMAT header so the read understands
    it), the date printed once per `== YYYY-MM-DD ==` day block, lines as
    `#id HH:MM X: body`. #ids are preserved, so [#id] citations still resolve."""
    order = []
    for m in messages:
        if m.sender not in order:
            order.append(m.sender)
    tok = {name: _sender_token(i) for i, name in enumerate(order)}
    legend = {tok[name]: name for name in order}

    leg = ", ".join(f"{t}={n}" for t, n in legend.items())
    out = ["FORMAT: lines are `#id HH:MM X: text`, grouped under `== YYYY-MM-DD ==` day "
           f"headers. Senders: {leg}. Cite any message by its #id, written [#id]."]
    cur_day = None
    for m in messages:
        ts = m.ts or ""
        # ts is normalised to "YYYY-MM-DD HH:MM"; fall back gracefully if it isn't.
        if len(ts) >= 16 and ts[4] == "-" and ts[7] == "-":
            day, tm = ts[:10], ts[11:16]
        else:
            day, tm = "", ts
        if day and day != cur_day:
            out.append(f"== {day} ==")
            cur_day = day
        time_part = f"{tm} " if tm else ""
        out.append(f"#{m.id} {time_part}{tok.get(m.sender, '?')}: {_body_of(m, media)}")
    return "\n".join(out), legend


def assemble_for_read(messages, media: dict) -> str:
    """The transcript that crosses to the read — compact when COMPACT_TRANSCRIPT is on,
    else the full form. (Receipts/citations use messages.json, so they're unaffected.)"""
    if settings.compact_transcript:
        return assemble_compact(messages, media)[0]
    return assemble(messages, media)


def stats(messages, media: dict) -> dict:
    from collections import Counter
    senders = Counter(m.sender for m in messages)
    n_media = sum(len(m.media) for m in messages)
    n_decoded = sum(1 for v in media.values()
                    if v.get("caption") or v.get("transcript") or v.get("tag") or v.get("explicit"))
    return {
        "messages": len(messages),
        "date_range": [messages[0].ts, messages[-1].ts] if messages else [],
        "senders": dict(senders),
        "media_attached": n_media,
        "media_decoded": n_decoded,
    }
