// Node-level test of the client-side export slicer (zip.js is isomorphic and
// node >= 20 has File/Blob, so the exact browser code runs here).
//
//     cd frontend && npx tsx slicer.test.mts [path-to-real-export.zip capMB]
//
// Without args: synthetic WhatsApp + Telegram exports. With a real zip + cap:
// slices it (tail) and writes /tmp/sliced-export.zip for an upload smoke test.

import { BlobReader, BlobWriter, TextReader, TextWriter, ZipReader, ZipWriter } from "@zip.js/zip.js";
import { openExport, planWindow, rangeBytes, rangeLabel, buildSlice } from "./src/lib/slicer";
import { readFileSync, writeFileSync } from "node:fs";

let passed = 0;
function check(name: string, ok: boolean, detail = "") {
  console.log(`  ${ok ? "ok  " : "FAIL"} ${name}${!ok && detail ? " — " + detail : ""}`);
  if (!ok) process.exit(1);
  passed++;
}

const MB = 1024 * 1024;

async function makeZip(files: Record<string, string | Uint8Array>): Promise<File> {
  const w = new ZipWriter(new BlobWriter("application/zip"), { level: 0 });
  for (const [name, data] of Object.entries(files)) {
    await w.add(name, typeof data === "string" ? new TextReader(data) : new BlobReader(new Blob([data])));
  }
  return new File([await w.close()], "export.zip");
}

function waChat(nMsgs: number, mediaEvery: number): { text: string; media: string[] } {
  const lines: string[] = [];
  const media: string[] = [];
  for (let i = 0; i < nMsgs; i++) {
    const day = 1 + (i % 28), month = 1 + Math.floor(i / 10) % 12;
    const ts = `${String(day).padStart(2, "0")}.${String(month).padStart(2, "0")}.2024, 10:${String(i % 60).padStart(2, "0")}:00`;
    if (i % mediaEvery === 0) {
      const f = `${String(i).padStart(8, "0")}-PHOTO-2024-01-01.jpg`;
      media.push(f);
      lines.push(`[${ts}] Anna: ‎<attached: ${f}>`);
    } else {
      lines.push(`[${ts}] Marco: message number ${i}`);
      if (i % 7 === 0) lines.push("a continuation line for the previous message");
    }
  }
  return { text: lines.join("\n"), media };
}

async function testWhatsApp() {
  console.log("[wa] synthetic export: 60 msgs, 12 x 1MB media, cap 6MB");
  const { text, media } = waChat(60, 5);
  const files: Record<string, string | Uint8Array> = { "chat/_chat.txt": text };
  for (const f of media) files[`chat/${f}`] = new Uint8Array(1 * MB);
  const zip = await makeZip(files);

  const model = await openExport(zip, "whatsapp");
  check("parsed all messages", model.msgs.length === 60, `${model.msgs.length}`);
  check("media mapped", model.msgs.filter((m) => m.media.length).length === 12);

  const budget = 6 * MB;
  const [f1, t1] = planWindow(model, budget, "tail");
  check("tail plan fits", rangeBytes(model, f1, t1) <= budget && t1 === 60 && f1 > 0, `[${f1},${t1})`);
  const [f2, t2] = planWindow(model, budget, "head");
  check("head plan fits", rangeBytes(model, f2, t2) <= budget && f2 === 0 && t2 < 60, `[${f2},${t2})`);
  const [f3, t3] = planWindow(model, budget, "middle");
  check("middle plan fits", rangeBytes(model, f3, t3) <= budget && f3 > 0 && t3 < 60, `[${f3},${t3})`);

  const { file: sliced, range } = await buildSlice(model, f1, t1);
  check("slice under cap", sliced.size <= budget + MB, `${(sliced.size / MB).toFixed(1)}MB`);
  check("range label", /^\d{4}-\d{2}-\d{2} → \d{4}-\d{2}-\d{2}$/.test(range), range);

  const rd = new ZipReader(new BlobReader(sliced));
  const entries = await rd.getEntries();
  const names = entries.map((e) => e.filename);
  check("chat file kept at original path", names.includes("chat/_chat.txt"));
  const keptMedia = names.filter((n) => n.endsWith(".jpg"));
  const expected = new Set(model.msgs.slice(f1, t1).flatMap((m) => m.media));
  check("exactly the in-range media", keptMedia.length === expected.size &&
        keptMedia.every((n) => expected.has(n)), `${keptMedia.length} vs ${expected.size}`);
  const chatEntry = entries.find((e) => e.filename === "chat/_chat.txt")!;
  // @ts-expect-error narrow at runtime
  const outText: string = await chatEntry.getData(new TextWriter());
  check("chat text truncated", outText.split("\n").length < text.split("\n").length &&
        outText.includes(`message number ${f1 + 1}`) === (model.msgs[f1 + 1]?.media.length === 0));
}

async function testTelegram() {
  console.log("[tg] synthetic export: 40 msgs, 8 x 1MB media, cap 5MB");
  const rows: Record<string, unknown>[] = [];
  const files: Record<string, string | Uint8Array> = {};
  for (let i = 0; i < 40; i++) {
    const row: Record<string, unknown> = {
      id: i, type: "message", from: "Anna",
      date: `2024-${String(1 + Math.floor(i / 4)).padStart(2, "0")}-15T10:00:0${i % 10}`,
      text: `msg ${i}`,
    };
    if (i % 5 === 0) {
      row.photo = `photos/photo_${i}.jpg`;
      files[`Export/photos/photo_${i}.jpg`] = new Uint8Array(1 * MB);
    }
    rows.push(row);
  }
  files["Export/result.json"] = JSON.stringify({ name: "test", type: "personal_chat", messages: rows });
  const zip = await makeZip(files);

  const model = await openExport(zip, "telegram");
  check("parsed rows", model.msgs.length === 40, `${model.msgs.length}`);
  const budget = 5 * MB;
  const [f, t] = planWindow(model, budget, "tail");
  check("tail plan fits", rangeBytes(model, f, t) <= budget && t === 40 && f > 0, `[${f},${t})`);

  const { file: sliced } = await buildSlice(model, f, t);
  check("slice under cap", sliced.size <= budget + MB, `${(sliced.size / MB).toFixed(1)}MB`);
  const rd = new ZipReader(new BlobReader(sliced));
  const entries = await rd.getEntries();
  const resEntry = entries.find((e) => e.filename === "Export/result.json")!;
  // @ts-expect-error narrow at runtime
  const outJson = JSON.parse(await resEntry.getData(new TextWriter()));
  check("result.json truncated to range", outJson.messages.length === t - f,
        `${outJson.messages.length} vs ${t - f}`);
  check("json shape preserved", outJson.name === "test" && outJson.type === "personal_chat");
}

async function testReal(path: string, capMB: number) {
  console.log(`[real] ${path} with cap ${capMB}MB`);
  const buf = readFileSync(path);
  const file = new File([buf], "export.zip");
  const t0 = Date.now();
  const model = await openExport(file, "whatsapp");
  console.log(`  parsed ${model.msgs.length} msgs, ${model.mediaBytes.size} entries in ${Date.now() - t0}ms`);
  const budget = Math.floor(capMB * MB * 0.97);
  const [f, t] = planWindow(model, budget, "tail");
  console.log(`  tail window [${f},${t}) = ${rangeLabel(model, f, t)} ≈ ${(rangeBytes(model, f, t) / MB).toFixed(0)}MB`);
  check("real: plan fits", rangeBytes(model, f, t) <= budget && t === model.msgs.length);
  const { file: sliced, range } = await buildSlice(model, f, t);
  check("real: slice under cap", sliced.size <= capMB * MB, `${(sliced.size / MB).toFixed(0)}MB`);
  writeFileSync("/tmp/sliced-export.zip", Buffer.from(await sliced.arrayBuffer()));
  console.log(`  wrote /tmp/sliced-export.zip (${(sliced.size / MB).toFixed(0)}MB, range ${range})`);
}

const [, , realPath, cap] = process.argv;
if (realPath) {
  await testReal(realPath, +cap || 100);
} else {
  await testWhatsApp();
  await testTelegram();
}
console.log(`\nALL ${passed} CHECKS PASS`);
