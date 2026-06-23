import { useEffect, useRef, useState } from "react";
import {
  Monitor,
  Server,
  Share2,
  Trash2,
  FileArchive,
  FileText,
  Image as ImageIcon,
} from "lucide-react";
import { getConfig } from "../api";
import { thumb } from "../lib/ascii";
import type { ReadRoute } from "../types";

interface Cfg {
  hosted: boolean;
  route: ReadRoute | null;
}

const CAPS = [
  "a cartoon cat covering its face",
  "two people at a table",
  "a screenshot of an app",
  "a blurred street at night",
  "a plate of food, from above",
  "a dog mid-jump on grass",
];

const NS = "http://www.w3.org/2000/svg";
const clamp = (v: number) => Math.max(0, Math.min(1, v));

// Where the custody narration steps fire (seconds). Text is built from the live
// config at fire-time, so the copy can never overstate what the backend does.
const FOOT_T = [0.2, 1.0, 1.7, 2.9, 3.4, 8.0, 8.9, 10.3];
function footStep(i: number, c: Cfg): [string, string, string] {
  const where = c.hosted ? "on the server" : "on this machine";
  const provider = c.route?.third_party ? "openrouter" : "your vps";
  const model = c.route?.model || "the model";
  const ret = c.route?.zero_retention ? " — no retention" : "";
  switch (i) {
    case 0: return ["", "this is you, with your exported chat.", ""];
    case 1: return ["", c.hosted ? "our server comes online." : "your machine does the work.", ""];
    case 2: return ["01", `you upload — the .zip is read ${where}.`, ""];
    case 3: return ["02", `images are decoded ${where} — and never sent.`, ""];
    case 4: return ["03", `only the text transcript goes to ${model} via ${provider}${ret}.`, ""];
    case 5: return ["04", "analysis complete.", "green"];
    case 6: return ["05", "the read comes back to you.", "green"];
    default: return ["06", "the raw images & messages are destroyed — nothing remains.", "red"];
  }
}

// The choreographed custody animation (~12.8s loop), ported from the design
// prototype's requestAnimationFrame logic into a React effect (cancelled on close).
export default function DataFlowModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const stageRef = useRef<HTMLDivElement>(null);
  const scalerRef = useRef<HTMLDivElement>(null);
  const footRef = useRef<HTMLDivElement>(null);
  const [cfg, setCfg] = useState<Cfg>({ hosted: false, route: null });
  const cfgRef = useRef<Cfg>(cfg);
  useEffect(() => { cfgRef.current = cfg; }, [cfg]);

  useEffect(() => {
    if (!open) return;
    getConfig()
      .then((c) => {
        const rs = c.routes ?? [];
        setCfg({ hosted: c.hosted, route: rs.find((r) => r.id === c.default_route) ?? rs[0] ?? null });
      })
      .catch(() => undefined);
  }, [open]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === "Escape" && open) onClose(); }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  useEffect(() => {
    const stage = stageRef.current, scaler = scalerRef.current, foot = footRef.current;
    if (!open || !stage || !scaler || !foot) return;
    const q = <T extends Element>(id: string) => stage.querySelector<T>("#" + id)!;

    const you = q<HTMLElement>("df_you"), server = q<HTMLElement>("df_server"), or = q<HTMLElement>("df_openrouter");
    const carousel = q<HTMLElement>("df_carousel"), strip = q<HTMLElement>("df_strip"), clabel = q<HTMLElement>("df_clabel");
    const prog = q<HTMLElement>("df_prog"), trash = q<HTMLElement>("df_trash"), trashlab = q<HTMLElement>("df_trashlab");
    const zip = q<HTMLElement>("df_zip"), doc = q<HTMLElement>("df_doc"), bigimg = q<HTMLElement>("df_bigimg");
    const laneTop = q<SVGPathElement>("df_laneTop"), laneBot = q<SVGPathElement>("df_laneBot");
    const labTop = q<SVGTextElement>("df_labTop"), labBot = q<SVGTextElement>("df_labBot");
    const arcZip = q<SVGPathElement>("df_arcZip"), arcDoc = q<SVGPathElement>("df_arcDoc");
    const bitsG = q<SVGGElement>("df_bits");

    // ---- coordinates / paths (row layout) ----
    const L: Record<string, number[]> = { you: [24, 120], server: [400, 122], or: [806, 128] };
    const imgsX = 500, imgsTop = 248, barTop = 77;
    const AO: Record<string, number[]> = { you: [76, 30], server: [100, 28], or: [86, 22] };
    const anchor = (n: string) => [L[n][0] + AO[n][0], L[n][1] + AO[n][1]];
    const arcPath = (a: number[], b: number[], lift: number) =>
      `M${a[0]} ${a[1]} Q ${(a[0] + b[0]) / 2} ${(a[1] + b[1]) / 2 - lift} ${b[0]} ${b[1]}`;
    const lanePath = (a: number[], b: number[], off: number) => {
      const dx = b[0] - a[0], dy = b[1] - a[1], len = Math.hypot(dx, dy) || 1, ux = dx / len, uy = dy / len, inset = 66;
      const ax = a[0] + ux * inset, ay = a[1] + uy * inset, bx = b[0] - ux * inset, by = b[1] - uy * inset, px = -uy * off, py = ux * off;
      return `M${ax + px} ${ay + py} L${bx + px} ${by + py}`;
    };
    let DROP = { from: [0, 0], to: [0, 0] };
    function layout() {
      you.style.left = L.you[0] + "px"; you.style.top = L.you[1] + "px";
      server.style.left = L.server[0] + "px"; server.style.top = L.server[1] + "px";
      or.style.left = L.or[0] + "px"; or.style.top = L.or[1] + "px";
      carousel.style.left = imgsX - 150 + "px"; carousel.style.top = imgsTop + "px";
      trash.style.left = imgsX - 60 + "px"; trash.style.top = imgsTop + "px";
      prog.style.left = L.server[0] + "px"; prog.style.top = barTop + "px";
      const yP = anchor("you"), sP = anchor("server"), oP = anchor("or");
      arcZip.setAttribute("d", arcPath(yP, sP, 80)); arcDoc.setAttribute("d", arcPath(sP, yP, 80));
      laneTop.setAttribute("d", lanePath(sP, oP, -13)); laneBot.setAttribute("d", lanePath(sP, oP, 13));
      const mid = [(sP[0] + oP[0]) / 2, (sP[1] + oP[1]) / 2];
      labTop.setAttribute("x", String(mid[0])); labTop.setAttribute("y", String(mid[1] - 22));
      labBot.setAttribute("x", String(mid[0])); labBot.setAttribute("y", String(mid[1] + 30));
      DROP = { from: [sP[0], sP[1] + 18], to: [imgsX, imgsTop + 23] };
    }

    // ---- carousel tiles ----
    const N = 6, tileW = 80, step = 100, center = 150, speed = 42;
    strip.innerHTML = "";
    const TILES: { el: HTMLElement; pre: HTMLElement; cap: string }[] = [];
    for (let i = 0; i < N; i++) {
      const d = document.createElement("div"); d.className = "tile";
      const pre = document.createElement("pre"); pre.textContent = thumb(i * 4 + 2, 14, 7);
      d.appendChild(pre); strip.appendChild(d); TILES.push({ el: d, pre, cap: CAPS[i % CAPS.length] });
    }
    function carousel_(ep: number) {
      const move = ep * speed; let best = 1e9, bestCap = "";
      for (let i = 0; i < N; i++) {
        let raw = i * step - (move % (N * step));
        if (raw < -step) raw += N * step; if (raw > N * step - step) raw -= N * step;
        const cx = raw + tileW / 2, dist = Math.abs(cx - center), sc = Math.max(0.55, 1.16 - dist / 300), t = TILES[i];
        t.el.style.left = raw + "px"; t.el.style.transform = "scale(" + sc.toFixed(3) + ")"; t.el.style.transformOrigin = "center top";
        t.el.style.opacity = (0.3 + 0.7 * Math.min(1, sc)).toFixed(2); t.pre.style.color = dist < 40 ? "#0a0a0a" : "#8a8a8a";
        if (dist < best) { best = dist; bestCap = t.cap; }
      }
      clabel.innerHTML = '“<span class="q">' + bestCap + '</span>”';
    }

    // ---- stream tokens ----
    bitsG.innerHTML = "";
    const mkbit = (cls: string, txt: string) => {
      const t = document.createElementNS(NS, "text"); t.setAttribute("class", "bit " + cls);
      t.setAttribute("text-anchor", "middle"); t.textContent = txt; bitsG.appendChild(t); return t;
    };
    const topBits: SVGTextElement[] = [], botBits: SVGTextElement[] = [];
    for (let i = 0; i < 5; i++) topBits.push(mkbit("", "msg") as SVGTextElement);
    for (let i = 0; i < 5; i++) botBits.push(mkbit("tx", "txt") as SVGTextElement);
    function streams(ep: number, on: boolean) {
      const L1 = laneTop.getTotalLength(), L2 = laneBot.getTotalLength(), sp = 70;
      topBits.forEach((b, k) => {
        if (!on) { b.style.opacity = "0"; return; }
        const p = laneTop.getPointAtLength((ep * sp + k * (L1 / 5)) % L1);
        b.setAttribute("x", String(p.x)); b.setAttribute("y", String(p.y + 3)); b.style.opacity = "1";
      });
      botBits.forEach((b, k) => {
        if (!on) { b.style.opacity = "0"; return; }
        const p = laneBot.getPointAtLength((ep * sp + k * (L2 / 5)) % L2);
        b.setAttribute("x", String(p.x)); b.setAttribute("y", String(p.y + 3)); b.style.opacity = "1";
      });
    }

    // ---- helpers ----
    const placeFly = (el: HTMLElement, path: SVGPathElement, t: number, sc: number) => {
      const p = path.getPointAtLength(path.getTotalLength() * clamp(t));
      el.style.left = p.x + "px"; el.style.top = p.y + "px"; el.style.transform = "translate(-50%,-50%) scale(" + sc + ")";
    };
    const pop = (el: HTMLElement, e: number, start: number, dur = 0.45) => {
      const a = clamp((e - start) / dur); el.style.opacity = String(a);
      el.style.transform = "scale(" + (0.82 + 0.18 * a).toFixed(3) + ")"; el.style.transformOrigin = "center center";
    };
    const appearVal = (e: number, start: number, dur = 0.45) => clamp((e - start) / dur);
    const progBar18 = (p: number, done: boolean) => {
      const W = 18, f = Math.round(p * W);
      return "[" + "█".repeat(f) + "░".repeat(W - f) + "] " + String(Math.round(p * 100)).padStart(3, " ") + "%" + (done ? " ✓" : "");
    };

    const T = { you: 0.2, server: 1.0, zip0: 1.7, zip1: 2.9, proc: 2.9, procEnd: 8.0, send0: 8.9, send1: 10.0, del0: 10.3, delEnd: 11.3, hold: 12.8 };

    function fit() { const w = scaler!.clientWidth, k = Math.min(1, w / 1000); stage!.style.transform = "scale(" + k + ")"; scaler!.style.height = 420 * k + "px"; }
    window.addEventListener("resize", fit);

    let raf = 0, active = true, t0 = performance.now(), footKey = "";
    function frame(now: number) {
      if (!active) return;
      let e = (now - t0) / 1000; if (e >= T.hold) { t0 = now; e = 0; }
      pop(you, e, T.you); pop(server, e, T.server); pop(or, e, T.proc + 0.05);
      carousel.style.opacity = String(e < T.procEnd ? appearVal(e, T.proc + 0.05, 0.5) : Math.max(0, 1 - (e - T.procEnd) / 0.4));
      const orA = String(appearVal(e, T.proc + 0.05, 0.5));
      laneTop.style.opacity = orA; laneBot.style.opacity = orA; labTop.style.opacity = orA; labBot.style.opacity = orA;
      if (e >= T.zip0 && e <= T.zip1) { const tz = (e - T.zip0) / (T.zip1 - T.zip0); placeFly(zip, arcZip, tz, 1); zip.style.opacity = String(tz > 0.9 ? 1 - (tz - 0.9) / 0.1 : 1); } else zip.style.opacity = "0";
      const ep = Math.max(0, e - T.proc); if (e >= T.proc) carousel_(ep); streams(ep, e >= T.proc + 0.05 && e < T.procEnd);
      if (e >= T.proc) { prog.style.opacity = "1"; const pr = clamp((e - T.proc) / (T.procEnd - T.proc)), dn = pr >= 1; prog.textContent = progBar18(pr, dn); prog.classList.toggle("done", dn); } else { prog.style.opacity = "0"; prog.classList.remove("done"); }
      if (e >= T.send0 && e <= T.send1) { const ts = (e - T.send0) / (T.send1 - T.send0); placeFly(doc, arcDoc, ts, 1); doc.style.opacity = String(ts < 0.1 ? ts / 0.1 : 1); } else doc.style.opacity = "0";
      if (e >= T.procEnd) {
        pop(trash, e, T.procEnd, 0.4);
        if (e >= T.del0) {
          const dl = clamp((e - T.del0) / (T.delEnd - T.del0));
          bigimg.style.left = DROP.from[0] + (DROP.to[0] - DROP.from[0]) * dl + "px";
          bigimg.style.top = DROP.from[1] + (DROP.to[1] - DROP.from[1]) * dl + "px";
          bigimg.style.transform = "translate(-50%,-50%) scale(" + (1.1 - 0.78 * dl).toFixed(3) + ")";
          bigimg.style.opacity = String(dl < 0.82 ? 1 : Math.max(0, 1 - (dl - 0.82) / 0.18));
          if (e > T.del0 + 0.15) { trash.classList.add("red"); trashlab.style.color = "var(--accent)"; }
        } else bigimg.style.opacity = "0";
      } else { trash.style.opacity = "0"; trash.classList.remove("red"); trashlab.style.color = ""; bigimg.style.opacity = "0"; }
      let idx = 0; for (let i = 0; i < FOOT_T.length; i++) if (e >= FOOT_T[i]) idx = i;
      const [n, text, color] = footStep(idx, cfgRef.current); const key = idx + "|" + text;
      if (footKey !== key) { footKey = key; foot!.className = "dffoot" + (color ? " " + color : ""); foot!.innerHTML = '<span class="n">' + (n || "·") + "</span>" + text; }
      raf = requestAnimationFrame(frame);
    }
    fit(); layout(); t0 = performance.now(); raf = requestAnimationFrame(frame);

    return () => { active = false; cancelAnimationFrame(raf); window.removeEventListener("resize", fit); };
  }, [open]);

  if (!open) return null;
  const serverLab = cfg.hosted ? "OUR SERVER" : "YOUR MACHINE";
  const serverSub = cfg.hosted ? "our website" : "local";
  const orLab = cfg.route?.third_party ? "OPENROUTER" : "YOUR VPS";
  const orSub = (cfg.route?.model || "llm") + (cfg.route?.zero_retention ? " · no retention" : "");
  const where = cfg.hosted ? "on our server" : "on your machine";

  return (
    <div className="modal open df-modal" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modalcard">
        <div className="modalhead">
          <span>how your data is processed</span>
          <button className="modalx" onClick={onClose}>[ esc ]</button>
        </div>
        <div className="df-h1">watch where it goes, and where it dies.</div>
        <div ref={scalerRef} id="df_scaler">
          <div ref={stageRef} id="df_stage">
            <svg className="fly" id="df_fly" viewBox="0 0 1000 420">
              <path className="lane" id="df_laneTop" d="" />
              <path className="lane" id="df_laneBot" d="" />
              <text className="laneLab" id="df_labTop" textAnchor="middle">messages →</text>
              <text className="laneLab" id="df_labBot" textAnchor="middle">← transcript</text>
              <g id="df_bits" />
              <path id="df_arcZip" d="" fill="none" stroke="none" />
              <path id="df_arcDoc" d="" fill="none" stroke="none" />
            </svg>
            <div className="node" id="df_you">
              <div className="framed"><Monitor size={54} strokeWidth={1.5} /></div>
              <div className="cap2"><span className="nlab">YOU</span><div className="nsub">your device</div></div>
            </div>
            <div className="node" id="df_server">
              <div className="framed"><Server size={46} strokeWidth={1.5} /></div>
              <div className="cap2"><span className="nlab">{serverLab}</span><div className="nsub">{serverSub}</div></div>
            </div>
            <div id="df_prog" />
            <div id="df_carousel">
              <div className="clab">parsing images · stays {where}</div>
              <div className="strip" id="df_strip" />
              <div className="clabel" id="df_clabel" />
            </div>
            <div className="node" id="df_openrouter">
              <div className="framed">
                <Share2 size={40} strokeWidth={1.4} />
                <div className="nlab">{orLab}</div>
                <div className="nsub">{orSub}</div>
              </div>
            </div>
            <div id="df_trash">
              <Trash2 size={46} strokeWidth={1.6} />
              <div className="nsub" id="df_trashlab" style={{ marginTop: "6px" }}>images + messages<br />destroyed</div>
            </div>
            <div className="flyobj" id="df_zip"><FileArchive size={42} strokeWidth={1.5} /><div className="objlab">.zip</div></div>
            <div className="flyobj" id="df_doc"><FileText size={42} strokeWidth={1.5} /><div className="objlab">read</div></div>
            <div className="flyobj" id="df_bigimg"><ImageIcon size={62} strokeWidth={1.4} /></div>
          </div>
        </div>
        <div className="dffoot" id="df_foot" ref={footRef} />
      </div>
    </div>
  );
}
