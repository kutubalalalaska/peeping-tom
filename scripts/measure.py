#!/usr/bin/env python3
"""Discovery / measurement for a chat export — sizes a corpus and recommends a read
tier (see SCALING.md). Runs the REAL ingest path (no decode, no model call), then
estimates the transcript token cost and which gate tier it lands in.

    cd mirror-app
    python3 scripts/measure.py /path/to/unzipped/export
    python3 scripts/measure.py /path/to/export --context 131072 --reserve 8000

Pass --context with GLM-5.2's real window once confirmed; the default is a flagged
placeholder. Source is auto-detected (result.json -> telegram, else _chat.txt).

MEMORY: telegram parsing json.loads the whole result.json. The script prints that
file's size first and warns before parsing if it's large (multi-GB RAM risk) — use
--no-parse to get sizes/media only, or install ijson for a streaming pass (TODO).
"""

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mirror import ingest, decode, transcript as T   # noqa: E402

CHARS_PER_TOKEN = 4          # rough, tokenizer-agnostic first-order estimate
COMPRESS_KEEP = 0.65         # ~35% lossless squeeze (Stage 2) — estimate
CHUNK_FILL = 0.60            # fraction of usable context per map-reduce chunk
PARSE_WARN_BYTES = 800 * 1024 * 1024


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024


def detect_source(folder: Path) -> str:
    if next(folder.rglob("result.json"), None):
        return "telegram"
    return "whatsapp"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="unzipped export directory (or the export file)")
    ap.add_argument("--source", choices=["auto", "telegram", "whatsapp"], default="auto")
    ap.add_argument("--context", type=int, default=131072,
                    help="model context window in tokens (PLACEHOLDER default — pass GLM-5.2's real value)")
    ap.add_argument("--reserve", type=int, default=8000,
                    help="tokens reserved for system prompt + read wrapper + output")
    ap.add_argument("--no-parse", action="store_true", help="sizes + media only; skip the (memory-heavy) parse")
    args = ap.parse_args()

    root = Path(args.path).expanduser()
    if not root.exists():
        sys.exit(f"path not found: {root}")
    folder = root if root.is_dir() else root.parent
    source = args.source if args.source != "auto" else detect_source(folder)

    print("=" * 64)
    print(f"  DISCOVERY · {root}")
    print(f"  source: {source}")
    print("=" * 64)

    export = ingest.find_export(folder, source) if root.is_dir() else root
    if not export:
        sys.exit(f"no {'result.json' if source == 'telegram' else '_chat.txt'} found under {folder}")
    export_bytes = export.stat().st_size
    print(f"\n[export file] {export.name}  ·  {human(export_bytes)}")

    # --- media inventory (cheap dir walk) ---
    media = ingest.media_files(folder, source)
    by_type, bytes_by_type = {}, {}
    total_media_bytes = 0
    for f in media:
        t = decode.file_type(f)
        by_type[t] = by_type.get(t, 0) + 1
        try:
            sz = f.stat().st_size
        except OSError:
            sz = 0
        bytes_by_type[t] = bytes_by_type.get(t, 0) + sz
        total_media_bytes += sz
    print(f"\n[media] {len(media)} files  ·  {human(total_media_bytes)} total")
    for t in sorted(by_type):
        print(f"    {t:10s} {by_type[t]:>7d}  ·  {human(bytes_by_type[t])}")

    if args.no_parse:
        print("\n[parse] skipped (--no-parse). Re-run without it for message count + token estimate.")
        return

    if source == "telegram" and export_bytes > PARSE_WARN_BYTES:
        print(f"\n[warn] result.json is {human(export_bytes)} — json.loads will spike RAM "
              f"(~3-5x). Ctrl-C to abort; consider --no-parse or streaming parse (ijson).")

    # --- parse (the memory-heavy step) ---
    print("\n[parse] reading the export…", flush=True)
    msgs, predecoded = ingest.parse_export(export, source)
    n = len(msgs)
    if not n:
        sys.exit("parsed 0 messages — wrong source? check the export.")
    span = f"{msgs[0].ts}  →  {msgs[-1].ts}"
    n_with_media = sum(1 for m in msgs if m.media)
    print(f"    messages: {n:,}")
    print(f"    span:     {span}")
    print(f"    media-bearing messages: {n_with_media:,}  ·  predecoded (tgs emoji): {len(predecoded)}")

    # --- transcript token estimate (text + media PLACEHOLDERS, the iterative-mode first read) ---
    # Measure BOTH the full and the real compact assembler (Stage 2), so the tier is
    # decided on the actual compressed size, not an estimate.
    text = T.assemble(msgs, {})           # empty media dict -> [image]/[voice message] placeholders
    compact, _legend = T.assemble_compact(msgs, {})
    chars, chars_c = len(text), len(compact)
    est = chars // CHARS_PER_TOKEN
    est_compressed = chars_c // CHARS_PER_TOKEN
    saved = (1 - chars_c / chars) * 100 if chars else 0
    print(f"\n[transcript] full (placeholder):  {chars:,} chars  ≈  {est:,} tokens (chars/{CHARS_PER_TOKEN})")
    print(f"             compact (Stage 2):   {chars_c:,} chars  ≈  {est_compressed:,} tokens  "
          f"(REAL -{saved:.0f}% lossless)")
    print(f"             (cheap-all mode adds caption text per image; iterative mode ≈ this + a bounded few)")

    # --- the gate (SCALING.md Stage 1) ---
    C, R = args.context, args.reserve
    U = C - R
    print(f"\n[gate] context C={C:,}  reserve R={R:,}  usable U={U:,}")
    if args.context == 131072:
        print("       (^ C is a PLACEHOLDER — re-run with GLM-5.2's real window via --context)")

    fits_raw = est <= U
    fits_comp = est_compressed <= U
    if fits_raw:
        tier = "1 · one-shot (raw fits — perfect fidelity)"
    elif fits_comp:
        tier = "2 · compress → one-shot (compression rescues it)"
    else:
        chunks = math.ceil(est_compressed / (CHUNK_FILL * U))
        tier = f"3 · map-reduce (~{chunks} chronological chunks + 1 synthesis)"
    print(f"\n  raw fits one-shot?        {'YES' if fits_raw else 'NO':3s}  ({est:,} vs U {U:,})")
    print(f"  compressed fits one-shot? {'YES' if fits_comp else 'NO':3s}  ({est_compressed:,} vs U {U:,})")
    print(f"\n  → RECOMMENDED TIER {tier}")
    print("\n  NOTE: token counts are chars/4 estimates — at a boundary, confirm with GLM's real")
    print("        tokenizer (a few % either way flips Tier 2 vs Tier 3).")
    print("=" * 64)


if __name__ == "__main__":
    main()
