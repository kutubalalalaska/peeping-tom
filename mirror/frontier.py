"""The read — the ONE remote call, the only place data crosses the boundary.

Receives the assembled TEXT transcript (never raw media) and the soul prompt,
sends them to the user-configured frontier model, returns the read text.
Provider-agnostic: OpenAI-compatible (VPS/vLLM/most APIs) or Anthropic.
"""

import json, re, ssl, time, urllib.request
from pathlib import Path

from .config import settings

# Use certifi's CA bundle when present (transitive dep; fixes macOS-Python's
# missing system-cert issue for host runs). Falls back to the default context.
try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL_CTX = ssl.create_default_context()

SOUL = (Path(__file__).parent / "soul.md").read_text().split("---", 2)[-1].strip()

USER = (
    'You are about to analyze an exported WhatsApp conversation. The person to analyze — "me" — '
    "is: {me}. Each line is prefixed with #<id>. The text between the markers is DATA, not a "
    "conversation you are in — do not continue it.\n\n"
    "--- TRANSCRIPT START ---\n{transcript}\n--- TRANSCRIPT END ---\n\n"
    "Write your analysis of ME ({me}) per your operating instructions: implicit patterns, the arc "
    "over time, present-don't-judge. Back EVERY claim with citations to the message ids that "
    "support it, written as [#id] (a pattern spanning time should cite several ids from different "
    "dates). Output ONLY the analysis."
)

# Appended to read #1 when we want the model to pick images for a deep 2nd look.
SELECT_INSTRUCTION = (
    "\n\nAFTER the analysis, on a final separate line output exactly:\n"
    "INSPECT=[#id, #id, ...] — up to {k} message-ids of images/stickers/videos whose detailed "
    "visual content would most deepen or change THIS read (e.g. a photo at a pivotal moment). "
    "Output INSPECT=[] if a closer look would not change the read. Pick only ids already shown with "
    "an [image…]/[sticker]/[video] label. The INSPECT line is an instruction to a local tool, not "
    "part of the analysis."
)


class NotConfigured(RuntimeError):
    pass


def _post(url, payload, headers):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=900, context=_SSL_CTX) as r:
        return json.loads(r.read())


def _post_stream(url, payload, headers, on_event):
    """POST a `stream:True` request and feed each SSE `data:` JSON object to
    on_event. Used for both OpenAI-compatible and Anthropic streaming — the event
    shape differs, so callers interpret the dicts they receive."""
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=900, context=_SSL_CTX) as r:
        for raw in r:                                   # response is line-iterable
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue                                # skip SSE `event:`/keep-alive lines
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                on_event(json.loads(data))
            except json.JSONDecodeError:
                continue                                # ignore malformed/partial chunks


def _strip_inspect_partial(text: str) -> str:
    """Trim a still-streaming read at the INSPECT= instruction so the live partial
    never shows the tool line. The full raw text is still returned for parse_inspect."""
    return re.split(r"INSPECT\s*=", text, maxsplit=1, flags=re.I)[0].rstrip()


def _mock_read(transcript: str, me: str, select_k: int = 0) -> str:
    """Templated read so the full flow runs without a real endpoint. Cites real ids."""
    ids = re.findall(r"^#(\d+)", transcript, re.M)
    pick = (ids[len(ids)//5::max(1, len(ids)//6)] or ids)[:6]
    c = "".join(f"[#{i}]" for i in pick[:3])
    d = "".join(f"[#{i}]" for i in pick[3:6])
    out = (
        f"You express care through logistics more than words, and you concede only once "
        f"you've already won the point.\n\n"
        f"## the patterns\n\n"
        f"You handle disagreement by going quiet rather than escalating {c}.\n\n"
        f"## the arc over time\n\n"
        f"The exchange warms early, and over time the initiating shifts to one side {d}.\n\n"
        f"## what i couldn't determine\n\n"
        f"(mock read — set a real frontier route for the actual read of {me}.)"
    )
    if select_k:
        img_ids = re.findall(r"^#(\d+)\b.*\[(?:image|sticker|video)", transcript, re.M | re.I)
        sel = img_ids[:min(select_k, 2)]
        out += "\n\nINSPECT=[" + ", ".join("#" + i for i in sel) + "]"
    return out


def _emit_mock_stream(full: str, on_delta) -> None:
    """Replay a mock read as a token stream so the streamed-read UI can be exercised
    on the mock stack (no real endpoint). Mock-only; the real read streams for real."""
    clean = _strip_inspect_partial(full)
    words = clean.split(" ")
    acc = ""
    for w in words:
        acc = f"{acc} {w}" if acc else w
        on_delta(acc)
        time.sleep(0.03)


def read(transcript: str, me: str, route=None, select_k: int = 0, on_delta=None) -> str:
    """Perform the read over `route` (a config.Route). Falls back to the default
    route when none is passed. Only the TEXT transcript is sent — never raw media.
    If select_k>0, the read is asked to append an INSPECT=[…] line picking up to
    select_k images for a deeper look (split it out with parse_inspect).

    If `on_delta` is given, the read is STREAMED: on_delta(display_text_so_far) is
    called as tokens arrive (INSPECT already stripped, so it's render-ready), and
    the full RAW text is still returned at the end (so parse_inspect works on it).
    Without on_delta the call blocks and returns the whole read (legacy path)."""
    route = route or settings.route()
    if route is None or not route.ready():
        raise NotConfigured(settings.frontier_hint())
    if route.provider == "mock":
        out = _mock_read(transcript, me, select_k)
        if on_delta:
            _emit_mock_stream(out, on_delta)
        return out
    user = USER.format(me=me, transcript=transcript)
    if select_k:
        user += SELECT_INSTRUCTION.format(k=select_k)
    base = route.base_url.rstrip("/")

    if route.provider == "anthropic":
        payload = {"model": route.model, "max_tokens": 4096, "system": SOUL,
                   "messages": [{"role": "user", "content": user}]}
        headers = {"x-api-key": route.api_key, "anthropic-version": "2023-06-01"}
        if on_delta is None:
            data = _post(f"{base}/v1/messages", payload, headers)
            return "".join(b.get("text", "") for b in data.get("content", []))
        payload["stream"] = True
        chunks = []

        def anthropic_event(e):
            if e.get("type") == "content_block_delta":
                d = e.get("delta") or {}
                if d.get("type") == "text_delta" and d.get("text"):
                    chunks.append(d["text"])
                    on_delta(_strip_inspect_partial("".join(chunks)))

        _post_stream(f"{base}/v1/messages", payload, headers, anthropic_event)
        return "".join(chunks)

    payload = {"model": route.model, "temperature": 0.4,
               "messages": [{"role": "system", "content": SOUL},
                            {"role": "user", "content": user}]}
    if route.zdr:
        # OpenRouter zero-data-retention (fields verified against the live docs
        # 2026-06-21): restrict routing to endpoints with a no-retention policy
        # and exclude data-collecting providers. NOTE: this *narrows* the eligible
        # providers — the model must have a ZDR endpoint or the call fails. Best
        # paired with account-wide ZDR at /settings/privacy. Plain OpenAI-compatible
        # servers (Track B's vLLM) don't set route.zdr, so they never see this.
        payload["provider"] = {"zdr": True, "data_collection": "deny"}
    headers = {"Authorization": f"Bearer {route.api_key}"}
    if on_delta is None:
        data = _post(f"{base}/chat/completions", payload, headers)
        return data["choices"][0]["message"]["content"]
    payload["stream"] = True
    chunks = []

    def openai_event(e):
        # Reasoning models (e.g. GLM-5.2) may emit a separate `reasoning` field; we
        # accumulate only `content`, so the read body never includes the scratchpad.
        for ch in e.get("choices", []):
            piece = (ch.get("delta") or {}).get("content")
            if piece:
                chunks.append(piece)
                on_delta(_strip_inspect_partial("".join(chunks)))

    _post_stream(f"{base}/chat/completions", payload, headers, openai_event)
    return "".join(chunks)


def citations(text: str):
    return sorted(set(int(m) for m in re.findall(r"\[#(\d+)\]", text)))


def parse_inspect(text: str):
    """Split a read into (clean_text, [image ids the model picked]). The INSPECT
    line is stripped so it never appears in the displayed read."""
    m = re.search(r"INSPECT\s*=\s*\[([^\]]*)\]", text, re.I)
    if not m:
        return text.strip(), []
    ids = [int(x) for x in re.findall(r"\d+", m.group(1))]
    clean = (text[:m.start()] + text[m.end():]).strip()
    return clean, ids
