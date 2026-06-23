#!/usr/bin/env python3
"""Probe whether a managed read route STREAMS reasoning tokens (the model's
chain-of-thought) — the spine of the live "thinking" view on the analysis screen.

GLM-5.2 is a reasoning model; OpenRouter *may* forward its reasoning as a separate
`reasoning` (or `reasoning_content`) delta alongside the answer `content`. We can't
know the exact shape without a real call, so this hits the live endpoint once and
reports what actually arrives, then tells you what to set.

Usage (from mirror-app/, with the route's env loaded from .env):

    cd mirror-app
    set -a && source .env && set +a
    python3 scripts/probe_reasoning.py                 # default: managed-api (Track A)
    python3 scripts/probe_reasoning.py <route-id>

Reads the env the same way the app does. One real (~cents) call. After it runs:
  • if `reasoning`/`reasoning_content` deltas arrive → set READ_STREAM_REASONING=1
    so the app shows the model's genuine reasoning (the NOTE preamble is the fallback).
  • if only `content` arrives → leave it off; the NOTE working-lines drive the view.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mirror.config import settings          # noqa: E402
from mirror import frontier                  # noqa: E402

TRANSCRIPT = "\n".join([
    "#1 [2024-01-02 09:14] Sam: morning! did you sleep ok?",
    "#2 [2024-01-02 09:41] Me: yeah fine. you?",
    "#3 [2024-01-05 22:10] Sam: you never said how the interview went",
    "#4 [2024-01-05 23:58] Me: [sticker: a cartoon cat hiding under a blanket]",
    "#5 [2024-01-11 18:02] Sam: ok i'm a little worried about you",
    "#6 [2024-01-11 18:03] Me: i'm fine!! just busy, promise",
])


def main() -> int:
    want = sys.argv[1] if len(sys.argv) > 1 else "managed-api"
    route = settings.route(want) or settings.route()
    if route is None or not route.ready():
        print(f"✗ No ready route '{want}'. Set its ROUTE_*/FRONTIER_* in .env, then "
              f"`set -a && source .env && set +a`.", file=sys.stderr)
        return 2
    if route.provider not in ("openai",):
        print(f"✗ Probe targets OpenAI-compatible/managed routes (got provider="
              f"{route.provider!r}). Reasoning capture for Anthropic uses thinking_delta.",
              file=sys.stderr)
        return 2

    user = frontier.USER.format(me="Me", transcript=TRANSCRIPT)
    payload = {"model": route.model, "temperature": 0.4, "stream": True,
               "messages": [{"role": "system", "content": frontier.SOUL},
                            {"role": "user", "content": user}],
               "reasoning": {"enabled": True}}        # the thing we're probing
    if route.zdr:
        payload["provider"] = {"zdr": True, "data_collection": "deny"}
    base = route.base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {route.api_key}"}

    print(f"Route : {route.id}  model={route.model}  zdr={route.zdr}")
    print("Streaming a real call with reasoning:{enabled:true} …\n")

    seen = {"reasoning": 0, "reasoning_content": 0, "content": 0}
    sample = {}
    t0 = time.monotonic()

    def ev(e):
        for ch in e.get("choices", []):
            d = ch.get("delta") or {}
            for k in seen:
                v = d.get(k)
                if v:
                    seen[k] += 1
                    sample.setdefault(k, v if isinstance(v, str) else str(v))

    try:
        frontier._post_stream(f"{base}/chat/completions", payload, headers, ev)
    except Exception as e:  # noqa: BLE001
        dt = time.monotonic() - t0
        print(f"✗ Stream failed after {dt:.1f}s: {type(e).__name__}: {e}", file=sys.stderr)
        print("  • 'reasoning' param may be rejected by this provider — try removing it "
              "(some endpoints stream reasoning by default), or pick a model with a ZDR endpoint.",
              file=sys.stderr)
        return 1

    dt = time.monotonic() - t0
    print(f"--- DELTA FIELD COUNTS (in {dt:.1f}s) ---")
    for k, n in seen.items():
        s = (sample.get(k, "") or "").replace("\n", " ")
        print(f"  {k:18} {n:5} deltas   {('e.g. ' + s[:70]) if s else ''}")

    got_reasoning = seen["reasoning"] + seen["reasoning_content"] > 0
    print()
    if got_reasoning:
        field = "reasoning" if seen["reasoning"] else "reasoning_content"
        print(f"✓ Reasoning IS streamed (as `{field}`). Set READ_STREAM_REASONING=1 — the "
              f"app will show the model's genuine chain-of-thought as the 'thinking' view.")
    else:
        print("• No reasoning deltas arrived (only `content`). Leave READ_STREAM_REASONING "
              "off; the prompted NOTE working-lines drive the 'thinking' view instead.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
