// Client-side export slicer: when a zip exceeds the upload cap, read it HERE
// (zip central directory + the chat file only — the media bytes stay untouched
// on disk), let the user pick a date window, and rebuild a smaller zip with
// just that window's messages + media. Nothing leaves the device until the
// user uploads the slice — a data-minimization feature, not a workaround:
// the server never receives what the user didn't choose to share.
//
// Works for both sources: WhatsApp (_chat.txt line format) and Telegram
// Desktop (result.json). zip64 exports (>4GB) are handled by zip.js.

import {
  BlobReader,
  BlobWriter,
  TextReader,
  TextWriter,
  ZipReader,
  ZipWriter,
  type FileEntry,
} from "@zip.js/zip.js";

export interface SliceMsg {
  date: number;          // epoch ms
  media: string[];       // zip entry names (full paths) attached to this message
  lineStart?: number;    // WhatsApp: raw line span incl. continuation lines
  lineEnd?: number;      //   (half-open)
}

export interface ExportModel {
  source: "whatsapp" | "telegram";
  chatEntry: FileEntry;
  msgs: SliceMsg[];
  mediaBytes: Map<string, number>;   // entry name -> uncompressed size
  entryByName: Map<string, FileEntry>;
  waLines?: string[];                // WhatsApp: the chat file's raw lines
  tgRoot?: Record<string, unknown>;  // Telegram: parsed result.json (messages swapped on build)
  tgRows?: unknown[];                // Telegram: raw message rows aligned with msgs? (see below)
}

const JUNK = (name: string) =>
  name.includes("__MACOSX") || name.split("/").pop()!.startsWith("._") ||
  name.endsWith(".DS_Store") || name.endsWith("/");

// ---- WhatsApp _chat.txt parsing (mirror of mirror/ingest.py, dates only) ----

const LRM = /[‎‏]/g;
const IOS_RE = /^\[([^\]]+)\]\s(.*)$/;
const ANDROID_RE =
  /^(\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4},?\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:[APMapm.]{2,4})?)\s-\s(.*)$/;
const ATTACH_RE = /<attached:\s*([^>]+)>/gi;
const TS_NUM_RE = /^(\d{1,4})[/.\-](\d{1,2})[/.\-](\d{1,4}),?\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([APMapm.]{2,4})?/;

function parseTs(ts: string, dayFirst: boolean): number | null {
  const m = ts.match(TS_NUM_RE);
  if (!m) return null;
  let [, a, b, c, hh, mm, ss, ap] = m;
  let day: number, mon: number, year: number;
  if (a.length === 4) {
    year = +a; mon = +b; day = +c;              // rare YYYY-first exports
  } else {
    year = +c < 100 ? 2000 + +c : +c;
    if (dayFirst) { day = +a; mon = +b; } else { mon = +a; day = +b; }
  }
  let h = +hh;
  if (ap) {
    const p = ap.toUpperCase().replace(/\./g, "");
    if (p.startsWith("P") && h < 12) h += 12;
    if (p.startsWith("A") && h === 12) h = 0;
  }
  const d = new Date(year, mon - 1, day, h, +mm, +(ss || 0));
  return isNaN(d.getTime()) ? null : d.getTime();
}

function detectDayFirst(lines: string[]): boolean {
  // If any first field exceeds 12 it must be the day; if any second field
  // does, it must be month-first. Ambiguous chats default to day-first
  // (matches most WhatsApp locales; a wrong guess only mislabels dates,
  // consistently, so the window itself still slices correctly).
  for (const raw of lines) {
    const line = raw.replace(LRM, "");
    const m = line.match(IOS_RE) || line.match(ANDROID_RE);
    if (!m) continue;
    const n = m[1].match(TS_NUM_RE);
    if (!n || n[1].length === 4) continue;
    if (+n[1] > 12) return true;
    if (+n[2] > 12) return false;
  }
  return true;
}

function parseWhatsApp(text: string, entryOfBase: Map<string, string>): { msgs: SliceMsg[]; lines: string[] } {
  const lines = text.split("\n");
  const dayFirst = detectDayFirst(lines);
  const msgs: SliceMsg[] = [];
  lines.forEach((raw, i) => {
    const line = raw.replace(LRM, "").trim();
    const m = line.match(IOS_RE) || line.match(ANDROID_RE);
    const attach = [...line.matchAll(ATTACH_RE)].map((a) => a[1].trim());
    if (m) {
      const ts = parseTs(m[1].replace(LRM, "").trim(), dayFirst);
      if (ts !== null) {
        if (msgs.length) msgs[msgs.length - 1].lineEnd = i;
        msgs.push({ date: ts, media: [], lineStart: i, lineEnd: lines.length });
      }
    }
    if (msgs.length && attach.length) {
      for (const a of attach) {
        const entry = entryOfBase.get(a.split("/").pop()!);
        if (entry) msgs[msgs.length - 1].media.push(entry);
      }
    }
  });
  return { msgs, lines };
}

// ---- open + parse -------------------------------------------------------------

export async function openExport(file: File, source: "whatsapp" | "telegram"): Promise<ExportModel> {
  const reader = new ZipReader(new BlobReader(file));
  const entries = (await reader.getEntries()).filter(
    (e): e is FileEntry => !e.directory && !JUNK(e.filename));
  const entryByName = new Map(entries.map((e) => [e.filename, e]));
  const entryOfBase = new Map<string, string>();
  const mediaBytes = new Map<string, number>();
  for (const e of entries) {
    entryOfBase.set(e.filename.split("/").pop()!, e.filename);
    mediaBytes.set(e.filename, e.uncompressedSize ?? 0);
  }

  const chatEntry =
    source === "telegram"
      ? entries.find((e) => e.filename.split("/").pop() === "result.json")
      : entries.find((e) => e.filename.split("/").pop() === "_chat.txt") ||
        entries.find((e) => e.filename.toLowerCase().endsWith(".txt"));
  if (!chatEntry) throw new Error("no chat file found in this zip");
  const text = await chatEntry.getData(new TextWriter());

  if (source === "telegram") {
    const root = JSON.parse(text) as Record<string, unknown>;
    const rows = (root.messages as unknown[]) ?? [];
    const dir = chatEntry.filename.split("/").slice(0, -1).join("/");
    const msgs: SliceMsg[] = [];
    const tgRows: unknown[] = [];
    for (const row of rows) {
      const r = row as Record<string, unknown>;
      const ts = Date.parse(String(r.date ?? ""));
      if (isNaN(ts)) continue;
      const media: string[] = [];
      const ref = (r.photo as string) || (r.file as string);
      if (ref) {
        const full = dir ? `${dir}/${ref}` : ref;
        const hit = entryByName.has(full) ? full
          : entryByName.has(ref) ? ref
          : entryOfBase.get(ref.split("/").pop()!);
        if (hit) media.push(hit);
      }
      msgs.push({ date: ts, media });
      tgRows.push(row);
    }
    return { source, chatEntry, msgs, mediaBytes, entryByName, tgRoot: root, tgRows };
  }

  const { msgs, lines } = parseWhatsApp(text, entryOfBase);
  return { source, chatEntry, msgs, mediaBytes, entryByName, waLines: lines };
}

// ---- window planning ------------------------------------------------------------

function msgBytes(model: ExportModel): number[] {
  return model.msgs.map((m) =>
    m.media.reduce((s, name) => s + (model.mediaBytes.get(name) ?? 0), 0));
}

export function rangeBytes(model: ExportModel, from: number, to: number): number {
  // Count each entry once (a file can be referenced twice in odd exports).
  const seen = new Set<string>();
  let total = 0;
  for (let i = from; i < to; i++) {
    for (const name of model.msgs[i].media) {
      if (!seen.has(name)) {
        seen.add(name);
        total += model.mediaBytes.get(name) ?? 0;
      }
    }
  }
  // + the chat file + per-entry zip overhead (headers + central directory)
  return total + (model.chatEntry.uncompressedSize ?? 0) + seen.size * 256;
}

export function planWindow(
  model: ExportModel,
  budget: number,
  anchor: "head" | "tail" | "middle"
): [number, number] {
  const bytes = msgBytes(model);
  const n = bytes.length;
  if (!n) return [0, 0];
  const fits = (f: number, t: number) => rangeBytes(model, f, t) <= budget;
  if (anchor === "tail") {
    let from = n;
    let acc = 0;
    while (from > 0 && acc + bytes[from - 1] <= budget) acc += bytes[--from];
    while (from < n && !fits(from, n)) from++;      // exact check (dedup + overhead)
    return [from, n];
  }
  if (anchor === "head") {
    let to = 0;
    let acc = 0;
    while (to < n && acc + bytes[to] <= budget) acc += bytes[to++];
    while (to > 0 && !fits(0, to)) to--;
    return [0, to];
  }
  // middle: expand symmetrically from the center
  let lo = Math.floor(n / 2), hi = lo;
  let acc = 0;
  while (lo > 0 || hi < n) {
    const takeLo = lo > 0 && (hi >= n || acc + bytes[lo - 1] <= acc + bytes[hi]);
    if (takeLo && acc + bytes[lo - 1] <= budget) { acc += bytes[--lo]; continue; }
    if (hi < n && acc + bytes[hi] <= budget) { acc += bytes[hi++]; continue; }
    break;
  }
  while (lo < hi && !fits(lo, hi)) (hi - lo) % 2 ? hi-- : lo++;
  return [lo, hi];
}

// ---- rebuild ---------------------------------------------------------------------

export function rangeLabel(model: ExportModel, from: number, to: number): string {
  if (from >= to) return "";
  const d = (ms: number) => new Date(ms).toISOString().slice(0, 10);
  return `${d(model.msgs[from].date)} → ${d(model.msgs[to - 1].date)}`;
}

export async function buildSlice(
  model: ExportModel,
  from: number,
  to: number,
  onProgress?: (done: number, total: number) => void
): Promise<{ file: File; range: string }> {
  // level 0 (store): media is already compressed; re-deflating GBs would only
  // burn CPU. The size estimate above assumes store, so it stays honest.
  const writer = new ZipWriter(new BlobWriter("application/zip"), { level: 0 });

  let chatText: string;
  if (model.source === "whatsapp") {
    const start = model.msgs[from].lineStart ?? 0;
    const end = model.msgs[to - 1].lineEnd ?? model.waLines!.length;
    chatText = model.waLines!.slice(start, end).join("\n");
  } else {
    const keep = new Set(model.tgRows!.slice(from, to));
    const rows = (model.tgRoot!.messages as unknown[]).filter((r) => keep.has(r));
    chatText = JSON.stringify({ ...model.tgRoot, messages: rows });
  }
  await writer.add(model.chatEntry.filename, new TextReader(chatText));

  const names: string[] = [];
  const seen = new Set<string>();
  for (let i = from; i < to; i++) {
    for (const name of model.msgs[i].media) {
      if (!seen.has(name)) { seen.add(name); names.push(name); }
    }
  }
  let done = 0;
  for (const name of names) {
    const entry = model.entryByName.get(name);
    if (entry) {
      const blob = await entry.getData(new BlobWriter());
      await writer.add(name, new BlobReader(blob));
    }
    onProgress?.(++done, names.length);
  }
  const blob = await writer.close();
  const range = rangeLabel(model, from, to);
  return { file: new File([blob], "sliced-export.zip", { type: "application/zip" }), range };
}
