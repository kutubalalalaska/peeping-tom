"""Ingest a chat export: unzip, find the export file, parse into messages.

Two sources, one output shape. WhatsApp ships a fragile `_chat.txt` we regex by
line; Telegram Desktop ships a structured `result.json` (exact ts + sender +
media paths) we read directly. Both produce the same list[Message], so decode,
transcript assembly, the read, and clickable receipts are all source-agnostic.

Each message gets a stable integer id (its position), used later as the [#id]
citation handle the read points back to.
"""

import json, os, re, zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

LRM, RLM, NBSP = "‎", "‏", " "
def _clean(s: str) -> str:
    return s.replace(LRM, "").replace(RLM, "").replace(NBSP, " ").strip()

IOS = re.compile(r"^\[(?P<ts>[^\]]+)\]\s(?P<rest>.*)$")
ANDROID = re.compile(
    r"^(?P<ts>\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4},?\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:[APMapm.]{2,4})?)\s-\s(?P<rest>.*)$")
ATTACH = re.compile(r"<attached:\s*(?P<f>[^>]+)>", re.I)

_TS_FORMATS = ("%d.%m.%Y, %I:%M:%S %p", "%d.%m.%Y, %H:%M:%S", "%m/%d/%y, %I:%M:%S %p",
               "%m/%d/%y, %H:%M", "%d/%m/%Y, %H:%M", "%d/%m/%Y, %I:%M:%S %p")


def _norm_ts(ts: str) -> str:
    for f in _TS_FORMATS:
        try:
            return datetime.strptime(ts, f).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    return ts


@dataclass
class Message:
    id: int
    ts: str
    sender: str
    text: str
    media: list = field(default_factory=list)   # attachment filenames


def unzip(zip_path: Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)
    return dest


def find_chat(folder: Path):
    return next(folder.rglob("_chat.txt"), None) or next(folder.rglob("*.txt"), None)


def find_export(folder: Path, source: str = "whatsapp"):
    """Locate the export file for a source: Telegram's result.json, else the
    WhatsApp _chat.txt."""
    if source == "telegram":
        return next(folder.rglob("result.json"), None)
    return find_chat(folder)


def parse_export(path: Path, source: str = "whatsapp"):
    """Unified entry → (messages, predecoded). `predecoded` carries media records
    we already know without the VLM — Telegram's animated-sticker emoji — keyed by
    basename, ready to merge into the decode output. Empty for WhatsApp."""
    if source == "telegram":
        return _parse_telegram(path)
    return parse(path), {}


def parse(chat_path: Path):
    """Return list[Message]. Continuation lines fold into the previous message."""
    msgs = []
    for raw in chat_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = _clean(raw)
        if not line:
            continue
        m = IOS.match(line) or ANDROID.match(line)
        if m:
            ts, rest = _clean(m.group("ts")), _clean(m.group("rest"))
            if ": " in rest:
                sender, text = rest.split(": ", 1)
            elif rest.endswith(":"):
                sender, text = rest[:-1], ""
            else:
                continue  # system line
            if "end-to-end encrypted" in text:
                continue
            media = [a.strip() for a in ATTACH.findall(text)]
            text = ATTACH.sub("", text).strip()
            msgs.append(Message(id=len(msgs), ts=_norm_ts(ts), sender=_clean(sender),
                                text=text, media=media))
        elif msgs:
            extra = ATTACH.findall(line)
            msgs[-1].media += [a.strip() for a in extra]
            add = ATTACH.sub("", line).strip()
            if add:
                msgs[-1].text = (msgs[-1].text + "\n" + add).strip()
    return msgs


# --- Telegram (Desktop result.json) ------------------------------------------

def _tg_text(t) -> str:
    """Flatten Telegram's `text`, which is a plain string OR a list mixing strings
    and entity objects (links, mentions, bold…). We keep only the text; styling
    carries little read value (JSON-only v1 decision)."""
    if isinstance(t, str):
        return t
    if isinstance(t, list):
        return "".join(seg if isinstance(seg, str) else seg.get("text", "")
                       for seg in t if isinstance(seg, (str, dict)))
    return ""


def _norm_tg_ts(ts: str) -> str:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S").strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return ts or ""


def _parse_telegram(path: Path):
    """Parse a Telegram Desktop JSON export into (messages, predecoded).

    Only `type == "message"` rows become messages; service rows (joins, calls,
    pins) are skipped, mirroring how WhatsApp system lines are dropped. The media
    reference is `photo` (images) or `file` (voice/video/sticker/document); we
    store its basename so decode/transcript key it the same way as WhatsApp. For
    `.tgs` animated stickers the VLM can't read Lottie, so we caption them from
    Telegram's `sticker_emoji` and hand that back as a predecoded record.
    Replies/forwards are ignored in v1 (kept to the shared Message shape)."""
    data = json.loads(Path(path).read_text(encoding="utf-8", errors="ignore"))
    msgs, predecoded = [], {}
    for m in data.get("messages", []):
        if m.get("type") != "message":
            continue
        sender = _clean(str(m.get("from") or ""))
        if not sender:
            continue  # author missing (rare in personal chats) — nothing to attribute
        text = _clean(_tg_text(m.get("text")))
        media = []
        ref = m.get("photo") or m.get("file")
        if ref:
            base = os.path.basename(ref)
            media.append(base)
            if m.get("media_type") == "sticker" and base.lower().endswith(".tgs"):
                emoji = (m.get("sticker_emoji") or "").strip()
                predecoded[base] = {"type": "sticker", "tier": "emoji",
                                    "caption": (f"animated sticker {emoji}".strip())}
        msgs.append(Message(id=len(msgs), ts=_norm_tg_ts(m.get("date")),
                            sender=sender, text=text, media=media))
    return msgs, predecoded


# Telegram export chrome that isn't chat media (JSON export is lean, but HTML
# leftovers can ride along if a user exported both formats).
_TG_SKIP_EXT = {".json", ".html", ".htm", ".css", ".js"}


def media_files(folder: Path, source: str = "whatsapp"):
    """All raw media files in the export (the bytes that stay local). Excludes the
    export's own text/markup — _chat.txt for WhatsApp, result.json + HTML chrome
    for Telegram — and macOS zip cruft (a `__MACOSX/` tree of `._name` AppleDouble
    sidecars that would otherwise be decoded as junk 'media')."""
    skip = _TG_SKIP_EXT if source == "telegram" else {".txt"}
    out = []
    for f in folder.rglob("*"):
        if not f.is_file() or f.suffix.lower() in skip:
            continue
        if f.name.startswith("._") or "__MACOSX" in f.parts:   # macOS AppleDouble / metadata
            continue
        out.append(f)
    return out


def participants(messages):
    """Distinct senders with message counts, most active first — powers the
    'which one is you?' role selector. Available the instant parsing finishes."""
    from collections import Counter
    c = Counter(m.sender for m in messages)
    return [{"name": n, "count": k} for n, k in c.most_common()]
