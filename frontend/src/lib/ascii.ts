// Pure ASCII generators — lifted from the design prototype. All "imagery" in the
// UI is text: image density-ramps, audio waveforms, a transport bar, the progress
// bar, the braille spinner. Seeded by filename so each item gets a stable pattern.

export const SPIN = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

const RAMP = " .:-=+*#%@";
const WAVE = " ▁▂▃▄▅▆▇█";

// Deterministic seed from a string (e.g. a filename).
export function seedOf(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h) % 997;
}

// Image → an ASCII density block (decorative, stable per seed).
export function thumb(seed: number, w = 24, h = 6): string {
  const r = RAMP;
  const o: string[] = [];
  for (let y = 0; y < h; y++) {
    let l = "";
    for (let x = 0; x < w; x++) {
      const v =
        Math.sin((x + seed) * 0.7) +
        Math.cos((y - seed) * 0.9) +
        Math.sin((x * y + seed * 2) * 0.21);
      l += r[Math.max(0, Math.min(r.length - 1, Math.floor(((v + 3) / 6) * (r.length - 1))))];
    }
    o.push(l);
  }
  return o.join("\n");
}

// Audio → a waveform row.
export function wave(seed: number, w = 18): string {
  let o = "";
  for (let x = 0; x < w; x++) {
    const v = (Math.sin((x + seed) * 0.8) + 1) / 2;
    o += WAVE[1 + Math.floor(v * 7)];
  }
  return o;
}

// Audio transport line, animated by a step-frame counter.
export function player(dur: number, sf: number): string {
  const f = sf % 11;
  const b = "▮".repeat(f) + "▯".repeat(10 - f);
  return "▶  " + b + "  0:" + String(Math.round((f / 10) * dur)).padStart(2, "0") + " / 0:" + String(dur).padStart(2, "0");
}

// ASCII progress bar: [████████░░░░] 56%
export function progBar(pct: number, width = 28): string {
  const p = Math.max(0, Math.min(100, pct));
  const f = Math.round((p / 100) * width);
  return "[" + "█".repeat(f) + "░".repeat(width - f) + "] " + String(Math.round(p)).padStart(3, " ") + "%";
}

// Indeterminate "working" bar: a lit window slides across, driven by the frame
// counter. Used where there's no numeric progress (the read, the deep pass).
export function scanBar(sf: number, width = 28, win = 5): string {
  const pos = sf % (width + win);
  let s = "";
  for (let i = 0; i < width; i++) s += i >= pos - win && i < pos ? "█" : "░";
  return "[" + s + "]";
}
