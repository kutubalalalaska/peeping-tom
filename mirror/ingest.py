"""Ingest a WhatsApp export: unzip, find _chat.txt, parse into messages.

Each message gets a stable integer id (its position), used later as the [#id]
citation handle the read points back to.
"""

import re, zipfile
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


def media_files(folder: Path):
    """All non-text files in the export (the raw media that stays local)."""
    return [f for f in folder.rglob("*") if f.is_file() and f.suffix.lower() != ".txt"]


def participants(messages):
    """Distinct senders with message counts, most active first — powers the
    'which one is you?' role selector. Available the instant parsing finishes."""
    from collections import Counter
    c = Counter(m.sender for m in messages)
    return [{"name": n, "count": k} for n, k in c.most_common()]
