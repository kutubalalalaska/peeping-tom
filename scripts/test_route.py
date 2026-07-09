#!/usr/bin/env python3
"""Verify ANY configured read route end-to-end, with no Docker or Ollama.

Runs the REAL read path — `frontier.read` with the real soul.md system prompt
(and, for managed APIs, the ZDR hint) — over a tiny synthetic transcript, and
times it. One real (~cents, or GPU-seconds) call confirms: the endpoint is
reachable, the model answers, and the read comes back with [#id] citations.
For self-host routes the elapsed time also surfaces cold-start latency.

Usage (from mirror-app/, with the route's env loaded from .env):

    cd mirror-app
    set -a && source .env && set +a
    python3 scripts/test_route.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mirror.config import settings          # noqa: E402  (after sys.path tweak)
from mirror import frontier                  # noqa: E402

# A small, deliberately telling transcript so the read has something real to cite.
TRANSCRIPT = "\n".join([
    "#1 [2024-01-02 09:14] Sam: morning! did you sleep ok?",
    "#2 [2024-01-02 09:41] Me: yeah fine. you?",
    "#3 [2024-01-05 22:10] Sam: you never said how the interview went",
    "#4 [2024-01-05 23:58] Me: [sticker: a cartoon cat hiding under a blanket]",
    "#5 [2024-01-11 18:02] Sam: ok i'm a little worried about you",
    "#6 [2024-01-11 18:03] Me: i'm fine!! just busy, promise",
    "#7 [2024-02-02 09:15] Sam: morning :) sleep ok?",
    "#8 [2024-02-02 12:40] Me: ya",
])


def main() -> int:
    route = settings.route(sys.argv[1] if len(sys.argv) > 1 else None) or settings.route()
    if route is None:
        print("✗ No route configured. Set ROUTE_A_BASE_URL/ROUTE_A_MODEL/ROUTE_A_API_KEY "
              "in .env, then `set -a && source .env && set +a`.", file=sys.stderr)
        return 2
    print(f"Route   : {route.id}")
    print(f"Model   : {route.model}")
    print(f"Base URL: {route.base_url}")
    print(f"ZDR     : {route.zdr}")
    if not route.ready():
        print("✗ Route is not ready (missing base_url or model).", file=sys.stderr)
        return 2
    if route.provider == "mock":
        print("✗ This is the mock route — set the real ROUTE_A_* to test.",
              file=sys.stderr)
        return 2

    print("\nCalling the endpoint now (real call — billed ~cents)…")
    t0 = time.monotonic()
    try:
        out = frontier.read(TRANSCRIPT, "Me", route)
    except Exception as e:  # noqa: BLE001 — surface the raw failure
        dt = time.monotonic() - t0
        print(f"\n✗ Read failed after {dt:.1f}s: {type(e).__name__}: {e}",
              file=sys.stderr)
        print("  • 'no endpoints'/'no providers' → model has no ZDR endpoint "
              "(managed API): try ROUTE_*_ZDR=0 or another model.", file=sys.stderr)
        print("  • timeout/connection (self-host) → endpoint still spinning up or "
              "the base_url/key is wrong.", file=sys.stderr)
        return 1
    dt = time.monotonic() - t0

    cites = frontier.citations(out)
    print(f"\n--- READ (in {dt:.1f}s) ---\n" + out)
    print(f"\n--- CITATIONS --- {cites}")
    if not cites:
        print("\n⚠ No [#id] citations — the model ignored the citation instruction. "
              "Works, but worth noting for model choice.")
        return 0
    print(f"\n✓ Route '{route.id}' works: real read returned with citations in {dt:.1f}s.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
