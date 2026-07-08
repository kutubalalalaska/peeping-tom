import { useEffect, useState, type ReactNode } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { getStatus } from "../api";
import type { JobStatus, RecentItem } from "../types";
import Frame from "./Frame";
import { SPIN, seedOf, thumb, wave, player, progBar, scanBar } from "../lib/ascii";
import { useSpinFrame } from "../lib/hooks";
import { useT } from "../lib/i18n";

const tag = (t: string) =>
  t === "sticker" ? "stk" : t === "video" ? "vid" : t === "audio" ? "aud" : "img";

// Coarse, honest ETA formatting ("~3m left"). Rounds to keep it from looking falsely precise.
function fmtEta(s: number): string {
  if (s == null || s < 0) return "";
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.round(s / 60);
  return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

// One decoded item rendered as ASCII (the design replaces real imagery with text).
function Glimpse({ item, sf }: { item: RecentItem; sf: number }) {
  const seed = seedOf(item.file);
  if (item.type === "audio") {
    const dur = 12 + (seed % 40);
    return (
      <div className="glimpse" key={item.file}>
        <pre className="thumb pre">{wave(seed, 18) + "\n" + wave(seed + 5, 18)}</pre>
        <div className="tag">[aud] {item.file}</div>
        <div className="player">{player(dur, sf)}</div>
        {item.caption && <div className="cap">“{item.caption}”</div>}
      </div>
    );
  }
  return (
    <div className="glimpse" key={item.file}>
      <pre className="thumb pre">{thumb(seed, 24, 6)}</pre>
      <div className="tag">[{tag(item.type)}] {item.file}</div>
      {item.caption && <div className="cap">“{item.caption}”</div>}
    </div>
  );
}

// The live media feed: the latest decoded item large, a fading tail beneath it.
// Reused for the up-front pass (inspecting) AND the read's deep-look pass
// (analyzing) — the relocated "watch the mirror open the photos" spectacle.
function Carousel({ recent, sf, label }: { recent: RecentItem[]; sf: number; label: string }) {
  const { t } = useT();
  const latest = recent[recent.length - 1];
  return (
    <div className="pcontent">
      <div className="stage">
        {latest ? <Glimpse item={latest} sf={sf} /> : <div className="up">{t("insp.parsingShort")}</div>}
      </div>
      <div className="tail">
        <div className="lab">{label}</div>
        <div className="rows">
          {recent.slice(-4).map((it, i, arr) => (
            <div
              className="lrow"
              key={it.file}
              style={{ opacity: 0.25 + 0.75 * ((i + 1) / arr.length) }}
            >
              [{tag(it.type)}] {it.file}
              {it.caption ? ` “${it.caption.slice(0, 46)}”` : ""} ✓
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// Strip the tool-only INSPECT line and the [#id] citations (resolved into quote
// blocks later, in Result) so the streaming read reads as clean prose.
const tidy = (t: string) =>
  t.replace(/INSPECT\s*=\s*\[[^\]]*\]?/i, "").replace(/(?:\s*\[#\d+\])+/g, "");

// The read as it streams in (status.partial_read): the same chapter/teaser shape
// Result uses, with a caret at the end. Citations resolve on the result page.
function LiveRead({ text }: { text: string }) {
  const sections: { title: string | null; body: string }[] = [{ title: null, body: "" }];
  tidy(text)
    .split("\n")
    .forEach((line) => {
      const h = line.match(/^\s*##\s+(.*\S)\s*$/);
      if (h) sections.push({ title: h[1].trim(), body: "" });
      else sections[sections.length - 1].body += line + "\n";
    });
  const out: ReactNode[] = [];
  sections.forEach((sec, si) => {
    if (sec.title) out.push(<h2 className="chapter" key={"h" + si}>{sec.title}</h2>);
    sec.body.split(/\n\n+/).forEach((para, pi) => {
      const p = para.trim();
      if (p) {
        out.push(
          <p key={"p" + si + "_" + pi} className={si === 0 && !sec.title ? "teaser" : ""}>
            {p}
          </p>
        );
      }
    });
  });
  return (
    <div className="read">
      {out}
      <span className="cur" />
    </div>
  );
}

// The model's live "thinking" view (status.partial_thinking) — its process, not
// the finished prose. Shows honest motion during the wait instead of replaying a
// done read for effect. Handles both shapes the backend may send: a few short
// working-lines (NOTE-derived) or a longer raw reasoning stream — we render the
// tail either way so it stays compact.
function ThinkingTicker({ text, sf }: { text: string; sf: number }) {
  const { t } = useT();
  const spin = SPIN[sf % SPIN.length];
  const lines = text.split(/\n+/).map((l) => l.trim()).filter(Boolean);
  const asList = lines.length >= 2;
  const view = asList ? lines.slice(-5) : [text.length > 280 ? "…" + text.slice(-280) : text];
  return (
    <div className="thinking">
      <div className="th-head">{spin}&nbsp;&nbsp;{t("insp.thinking")}</div>
      <div className="th-lines">
        {view.map((l, i) => {
          const last = i === view.length - 1;
          return (
            <div
              key={i}
              className={"th-line" + (last ? " active" : "")}
              style={asList ? { opacity: 0.35 + 0.65 * ((i + 1) / view.length) } : undefined}
            >
              {l}
              {last && <span className="th-cur" />}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// Upload drives everything: parse/decode → the read, automatically (no identity
// pick, no send gate — v1; who's-who is deferred to v2). One screen spans
// uploaded → inspecting → analyzing, then navigates to /result on done.
export default function Inspection() {
  const { id } = useParams<{ id: string }>();
  const [s, setS] = useState<JobStatus | null>(null);
  const nav = useNavigate();
  const sf = useSpinFrame(true);
  const { t, tList } = useT();

  useEffect(() => {
    if (!id) return;
    const t = setInterval(async () => {
      let st: JobStatus;
      try {
        st = await getStatus(id);
      } catch {
        return;
      }
      setS(st);
      if (st.state === "done") {
        clearInterval(t);
        nav(`/result/${id}`);
      }
      if (st.state === "error" || st.state === "needs_config") clearInterval(t);
    }, 1000);
    return () => clearInterval(t);
  }, [id, nav]);

  const state = s?.state;
  const pct = s?.progress?.pct ?? 0;
  const done = s?.progress?.done ?? 0;
  const total = s?.progress?.total ?? 0;
  const recent = s?.recent ?? [];
  const eta = s?.eta_seconds ?? null;
  const spin = SPIN[sf % SPIN.length];

  if (state === "error") {
    return (
      <Frame step={t("insp.errorStep")} hero={t("insp.errorHero")}>
        <p className="err">{s?.message}</p>
      </Frame>
    );
  }
  if (state === "needs_config") {
    return (
      <Frame step={t("insp.configStep")} hero={t("insp.configHero")}>
        <p className="err">{s?.message}</p>
        <p className="hint2">{t("insp.configHint")}</p>
      </Frame>
    );
  }

  // analyzing → the read is being generated. Three beats, told apart by
  // status.message: reading the chat, opening the photos it flagged (the relocated
  // media spectacle), then re-reading with them in view.
  if (state === "analyzing") {
    // `rawMsg` is the backend's own status line (still English — see i18n phase 2);
    // it drives the deep-look detection below, so read it before falling back.
    const rawMsg = s?.message;
    const msg = rawMsg ?? t("insp.readingFallback");
    const thinking = s?.partial_thinking?.trim();
    const partial = s?.partial_read?.trim();
    // The deep-look sub-phase. The backend leaves partial_read holding the prior
    // read while it opens images, so the carousel (the relocated media spectacle)
    // must win over the stale partial here.
    const opening = /opening/i.test(rawMsg ?? "");
    const imgs = recent.filter((it) => it.type !== "audio");
    const showCarousel = opening && imgs.length > 0;
    const showRead = !showCarousel && !!partial;
    // Honest motion while we wait: the model's live thinking shows first, and the
    // read takes over the moment it actually starts writing.
    const showThinking = !showCarousel && !showRead && !!thinking;
    const tips = tList("insp.tips");
    const tip = tips[Math.floor(sf / 70) % tips.length]; // ~5s each
    return (
      <Frame
        step={t("insp.step4")}
        hero={showCarousel ? t("insp.openingPhotos") : t("insp.readingChat")}
        top={showRead}
      >
        {showCarousel ? (
          <Carousel recent={imgs} sf={sf} label={t("insp.justOpened")} />
        ) : showRead ? (
          <LiveRead text={partial!} />
        ) : showThinking ? (
          <div className="pcontent">
            <ThinkingTicker text={thinking!} sf={sf} />
          </div>
        ) : (
          <div className="pcontent">
            <div className="up">
              {msg}
              <br />
              <span className="tip">{tip}</span>
            </div>
          </div>
        )}
        <div className="barrow">
          <span className="pre">{scanBar(sf)}</span>
          <span className="phase">
            {spin}&nbsp;&nbsp;{msg}
            {eta ? <span className="eta">  ·  {t("insp.etaLeft", { eta: fmtEta(eta) })}</span> : null}
          </span>
        </div>
      </Frame>
    );
  }

  // uploaded / inspecting → the local pass. With iterative discovery this is just
  // text + voice-note transcription (a quick "parsing" beat); images are opened
  // later, during the read. Legacy mode decodes all media here. Drive the framing
  // off what's actually surfaced so we never label an audio pass as image decode,
  // nor show an empty media stage.
  const uploading = state === "uploaded" || !state;
  const hasVisual = recent.some((it) => it.type !== "audio");
  const hero = uploading
    ? t("insp.uploadingHero")
    : hasVisual
    ? t("insp.decodingHero")
    : t("insp.parsingHero");
  const step = uploading
    ? t("insp.step3upload")
    : t(hasVisual ? "insp.step3decode" : "insp.step3parse");
  return (
    <Frame step={step} hero={hero} custody={t("insp.custodyLocal")}>
      {uploading ? (
        <div className="pcontent">
          <div className="up">
            {t("insp.uploadingBody1")}
            <br />
            {t("insp.uploadingBody2")}
          </div>
        </div>
      ) : recent.length ? (
        <Carousel recent={recent} sf={sf} label={t("insp.justDecoded")} />
      ) : (
        <div className="pcontent">
          <div className="up">
            {s?.message ?? t("insp.parsingFallback")}
            <br />
            {t("insp.parsingBody")}
          </div>
        </div>
      )}
      <div className="barrow">
        <span className="pre">{total ? progBar(pct) : scanBar(sf)}</span>
        <span className="phase">
          {spin}&nbsp;&nbsp;
          {uploading
            ? t("start.uploading")
            : total
            ? `${t(hasVisual ? "insp.phaseDecode" : "insp.phaseTranscribe")}  ${done}/${total}`
            : t("insp.parsingShort")}
          {eta ? <span className="eta">  ·  {t("insp.etaLeft", { eta: fmtEta(eta) })}</span> : null}
        </span>
      </div>
    </Frame>
  );
}
