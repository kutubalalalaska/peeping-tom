"""Token budgeting + the size gate (SCALING.md Stage 1).

Decides whether a transcript is read one-shot or via chronological MAP-REDUCE, and
where to cut the chunks. Token counts here are LANGUAGE-AWARE: `chars/4` is a
Latin-only heuristic that badly underestimates Cyrillic/CJK (our real 4-yr test chat
is 96% Cyrillic — `chars/4` was off ~1.75x). We estimate from the dominant script
with a safety margin and route CONSERVATIVELY (prefer over-chunking to overflowing a
costly long-context call). Ground truth is the model tokenizer / the read's
`usage.prompt_tokens`; this is the cheap a-priori router that runs before any call.
"""

from . import transcript as T
from .config import settings

# chars-per-token by dominant script. Deliberately a touch low (⇒ token estimate a
# touch high ⇒ conservative routing). Confirm against real usage when available.
_CPT = {"latin": 4.0, "cyrillic": 2.3, "cjk": 1.6, "other": 3.0}


def dominant_script(text: str, sample: int = 200_000) -> str:
    """Cheap dominant-script guess from a sample, counting letters only (spaces/digits/
    punctuation tokenize Latin-like in every script, so they'd wash out the signal)."""
    s = text[:sample]
    lat = cyr = cjk = 0
    for ch in s:
        cp = ord(ch)
        if 0x400 <= cp <= 0x4FF:
            cyr += 1
        elif (0x4E00 <= cp <= 0x9FFF) or (0x3040 <= cp <= 0x30FF) or (0xAC00 <= cp <= 0xD7A3):
            cjk += 1
        elif (65 <= cp <= 90) or (97 <= cp <= 122):
            lat += 1
    if not (lat or cyr or cjk):
        return "other"
    return max((("latin", lat), ("cyrillic", cyr), ("cjk", cjk)), key=lambda kv: kv[1])[0]


def estimate_tokens(text: str, script: str = None) -> int:
    """Language-aware token estimate. Applies the dominant-script divisor to the whole
    length (punctuation/digits make this slightly conservative — intended)."""
    cpt = _CPT.get(script or dominant_script(text), 3.0)
    return int(len(text) / cpt)


def _usable() -> int:
    return int((settings.read_context_tokens - settings.read_reserve_tokens) * settings.read_safety)


def plan(messages, media: dict) -> dict:
    """Decide how to read this corpus. Returns:
        {tier, form, chunks: [(start,end), ...], est_full, est_compact, usable, script}
    tier 1 = one-shot (form 'full' if it fits raw, else 'compact' if compression rescues
    it); tier 3 = map-reduce over chronological chunks (each assembled compact). Chunks
    are message-index half-open ranges sized to ~chunk_fill of the usable window."""
    U = _usable()
    full = T.assemble(messages, media)
    compact, _ = T.assemble_compact(messages, media)
    script = dominant_script(compact)
    est_full = estimate_tokens(full, script)
    est_compact = estimate_tokens(compact, script)

    base = {"est_full": est_full, "est_compact": est_compact, "usable": U, "script": script}
    if est_full <= U:
        return {**base, "tier": 1, "form": "full", "chunks": [(0, len(messages))]}
    if est_compact <= U:
        return {**base, "tier": 1, "form": "compact", "chunks": [(0, len(messages))]}

    # map-reduce: walk messages, cut a chunk when it reaches ~chunk_fill * U tokens.
    budget = max(1, int(settings.chunk_fill * U))
    cpt = _CPT.get(script, 3.0)
    chunks, start, acc = [], 0, 0
    for i, m in enumerate(messages):
        t = int(len(T._body_of(m, media)) / cpt) + 6      # +overhead for #id/time/sender
        if acc and acc + t > budget:
            chunks.append((start, i)); start, acc = i, 0
        acc += t
    chunks.append((start, len(messages)))
    return {**base, "tier": 3, "form": "compact", "chunks": chunks}
