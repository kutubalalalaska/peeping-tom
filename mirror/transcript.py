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
from .mediatypes import kind


def _mss(s) -> str:
    s = int(round(s or 0))
    return f"{s // 60}:{s % 60:02d}"


def _attach_label(fname: str, media: dict) -> str:
    """One media file → its transcript label. Decoded content wins; an undecoded
    file with manifest metadata renders as an informative placeholder the read
    can reason about (and request); bare labels are the last resort."""
    rec = media.get(fname) or media.get(os.path.basename(fname)) or {}
    k = kind(fname)
    if k == "audio":
        t = rec.get("transcript")
        if t:
            return f'[voice message: "{t}"]'
        if rec.get("seconds"):
            return f"[voice {_mss(rec['seconds'])} — undecoded]"
        return "[voice message]"
    if k == "sticker":
        c = rec.get("caption")
        return f"[sticker: {c}]" if c else "[sticker — undecoded]"
    if k == "video":
        c = rec.get("caption") or "; ".join(rec.get("frame_captions", []))
        if rec.get("transcript"):
            c = (c + " | said: " + rec["transcript"]) if c else "said: " + rec["transcript"]
        if c:
            return f"[video: {c}]"
        note = "video note" if rec.get("video_note") else "video"
        if rec.get("seconds"):
            return f"[{note} {_mss(rec['seconds'])} — undecoded]"
        if rec.get("bytes"):
            return f"[{note} {max(1, rec['bytes'] // 1_000_000)}MB — undecoded]"
        return f"[{note}]"
    if k == "image":
        if rec.get("explicit"):
            # Neutral marker only — the graphic caption is never produced/stored, so
            # nothing intimate crosses the boundary. The fact of the image is the signal.
            return f"[{rec.get('marker') or 'intimate/explicit image'}]"
        c = rec.get("caption") or rec.get("tag")
        if c:
            return f"[image: {c}]"
        if rec.get("w"):
            return f"[image {rec['w']}×{rec['h']} — undecoded]"
        return "[image]"
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


def _sender_tokens(order: list) -> dict:
    """Short but REAL sender tags: the first name, extended just enough to stay
    unique (surname initials, then the full name as a last resort). Single-letter
    A/B tokens saved a little more, but cost attribution accuracy — the model
    demonstrably mixed the letters up across a long read. A first name is
    self-evident on every line; no legend lookup to fumble."""
    toks, seen = {}, set()
    for name in order:
        words = name.split()
        base = words[0] if words else name
        cand = base
        for extra in range(1, len(words)):
            if cand.lower() not in seen:
                break
            cand = f"{base} {'.'.join(w[0] for w in words[1:extra + 1])}."
        if cand.lower() in seen:
            cand = name if name.lower() not in seen else f"{base}{len(seen)}"
        seen.add(cand.lower())
        toks[name] = cand
    return toks


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
    tok = _sender_tokens(order)
    legend = {tok[name]: name for name in order}

    shortened = ", ".join(f"{t} = {n}" for t, n in legend.items() if t != n)
    leg = f" Senders shortened to first names: {shortened}." if shortened else ""
    out = ["FORMAT: lines are `#id HH:MM sender: text`, grouped under `== YYYY-MM-DD ==` day "
           f"headers.{leg} Cite any message by its #id, written [#id]."]
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


def assemble_for_read(messages, media: dict, header: str = "") -> str:
    """The transcript that crosses to the read (+ an optional MEDIA MANIFEST
    header). Compact when COMPACT_TRANSCRIPT is on, else the full form.
    (Receipts/citations use messages.json, so they're unaffected.)"""
    body = assemble_compact(messages, media)[0] if settings.compact_transcript \
        else assemble(messages, media)
    return f"{header}\n\n{body}" if header else body


def render_evidence(msgs_by_id: dict, items: list) -> str:
    """Fold-round delta: evidence lines for freshly-decoded media, rendered as the
    same `#id [ts] sender: [label]` shape the read already knows. `items` are
    evidence records {file, ids, rec}."""
    lines, seen = [], set()
    for it in items:
        media_one = {it["file"]: it.get("rec") or {}}
        for i in it.get("ids") or []:
            m = msgs_by_id.get(i)
            if m is None or i in seen:
                continue
            seen.add(i)
            lines.append(f"#{m.id} [{m.ts}] {m.sender}: "
                         f"{(m.text + ' ') if m.text else ''}{_attach_label(it['file'], media_one)}")
    return "\n".join(lines)


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
