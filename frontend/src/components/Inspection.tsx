import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { getStatus } from "../api";
import type { JobStatus, MediaRequest, RecentItem } from "../types";
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
              key={it.file + "-" + i}
              style={{ opacity: 0.25 + 0.75 * ((i + 1) / arr.length) }}
            >
              [{tag(it.type)}] {it.file}
              {it.caption ? ` “${it.caption.slice(0, 46)}”` : ""}
              {it.reinspected ? " ↻" : " ✓"}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// The model's live "thinking" view (status.partial_thinking) — its process, not
// the finished prose. This is deliberately the STAR of the analyzing screen: the
// finished read is NOT streamed here (it would spoil the reveal on the Result
// page). Handles both shapes the backend may send: a few short working-lines
// (NOTE-derived) or a longer raw reasoning stream — we render the tail either way
// so it stays compact. `label` retitles it (e.g. "composing…") and `live` toggles
// the caret (off once the model has stopped thinking and is writing the read).
function ThinkingTicker({
  text,
  sf,
  label,
  live = true,
}: {
  text: string;
  sf: number;
  label?: string;
  live?: boolean;
}) {
  const { t } = useT();
  const spin = SPIN[sf % SPIN.length];
  const lines = text.split(/\n+/).map((l) => l.trim()).filter(Boolean);
  const asList = lines.length >= 2;
  const view = asList
    ? lines.slice(-5)
    : lines.length
    ? [text.length > 280 ? "…" + text.slice(-280) : text.trim()]
    : [];
  return (
    <div className="thinking">
      <div className="th-head">{spin}&nbsp;&nbsp;{label ?? t("insp.thinking")}</div>
      {view.length > 0 && (
        <div className="th-lines">
          {view.map((l, i) => {
            const last = i === view.length - 1;
            return (
              <div
                key={i}
                className={"th-line" + (last && live ? " active" : "")}
                style={asList ? { opacity: 0.35 + 0.65 * ((i + 1) / view.length) } : undefined}
              >
                {l}
                {last && live && <span className="th-cur" />}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// The read's media requests (fast mode): which messages it asked to open and
// WHY — the reason is the star. Statuses advance pending → decoding → done.
function Requests({ reqs }: { reqs: MediaRequest[] }) {
  const { t } = useT();
  const glyph = (s: MediaRequest["status"]) =>
    s === "done" ? "✓" : s === "skipped" ? "—" : s === "decoding" ? "…" : "○";
  return (
    <div className="requests">
      <div className="lab">{t("insp.requestsTitle")}</div>
      {reqs.map((r, i) => (
        <div className="req" key={i}>
          <span className="req-g">{glyph(r.status)}</span>
          {r.ids.slice(0, 5).map((id) => `#${id}`).join(" ")}
          {r.ids.length > 5 ? ` +${r.ids.length - 5}` : ""}
          {r.reason ? <span className="req-r"> — “{r.reason}”</span> : null}
        </div>
      ))}
    </div>
  );
}

// Upload drives everything: parse → (fast|deep) read, automatically. One screen
// spans uploading → inspecting → analyzing, rendered from the machine-readable
// status.phase, then navigates to /result on done.
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

  // analyzing → the read is running. Rendered from the machine-readable `phase`:
  //   reading    the model reads (text-first) — its live thinking is the star
  //   requesting it chose which media to open (a brief beat)
  //   decoding   the local decoder fulfils its requests — reasons + glimpses
  //   folding    (deep) new evidence is being folded into the working read
  //   composing  the final read is being written (spoiler-free recap)
  // The FINAL read is deliberately NOT streamed here — the reveal is /result.
  if (state === "analyzing") {
    const phase = s?.phase ?? "reading";
    const msg = s?.message ?? t("insp.readingFallback");
    const thinking = s?.partial_thinking?.trim();
    const reqs = (s?.media_requests ?? []).filter(Boolean);
    const foldRound = s?.fold?.round;
    const dec = s?.decode;
    const bgDecoding = !!dec && !s?.decode_done;
    const tips = tList("insp.tips");
    const tip = tips[Math.floor(sf / 70) % tips.length]; // ~5s each

    const hero =
      phase === "decoding"
        ? t("insp.decodingReqHero")
        : phase === "requesting"
        ? t("insp.requestingHero")
        : phase === "folding"
        ? t("insp.foldingHero")
        : t("insp.readingChat");

    const body =
      phase === "decoding" ? (
        <div className="pcontent">
          {recent.length > 0 && (
            <div className="stage">
              <Glimpse item={recent[recent.length - 1]} sf={sf} />
            </div>
          )}
          {reqs.length > 0 && <Requests reqs={reqs} />}
        </div>
      ) : phase === "composing" ? (
        <div className="pcontent">
          <ThinkingTicker text={thinking ?? ""} sf={sf} label={t("insp.composing")} live={false} />
        </div>
      ) : thinking ? (
        <div className="pcontent">
          <ThinkingTicker
            text={thinking}
            sf={sf}
            label={phase === "folding" && foldRound ? t("insp.foldRound", { round: foldRound }) : undefined}
          />
          {phase === "requesting" && reqs.length > 0 && <Requests reqs={reqs} />}
        </div>
      ) : (
        <div className="pcontent">
          <div className="up">
            {msg}
            <br />
            <span className="tip">{tip}</span>
          </div>
          {reqs.length > 0 && phase === "requesting" && <Requests reqs={reqs} />}
        </div>
      );

    const barPct = phase === "decoding" && total ? progBar(pct) : scanBar(sf);
    return (
      <Frame step={t("insp.step4")} hero={hero}>
        {body}
        <div className="barrow">
          <span className="pre">{barPct}</span>
          <span className="phase">
            {spin}&nbsp;&nbsp;{msg}
            {eta ? <span className="eta">  ·  {t("insp.etaLeft", { eta: fmtEta(eta) })}</span> : null}
          </span>
        </div>
        {bgDecoding && (
          <div className="bgbar">
            {t("insp.bgDecode", { done: dec!.done, total: dec!.total })}
            {dec!.reinspect && dec!.reinspect.total > 0
              ? `  ·  ${t("insp.phaseReinspect")} ${dec!.reinspect.done}/${dec!.reinspect.total}`
              : ""}
            {dec!.eta_seconds
              ? `  ·  ${t("insp.etaLeft", { eta: fmtEta(dec!.eta_seconds) })}`
              : ""}
          </div>
        )}
      </Frame>
    );
  }

  // uploading / inspecting → local, pre-read phases (parsing, manifest probe).
  const uploading = state === "uploaded" || state === "uploading" || !state;
  const hero = uploading ? t("insp.uploadingHero") : t("insp.parsingHero");
  const step = uploading ? t("insp.step3upload") : t("insp.step3parse");
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
          {uploading ? t("start.uploading") : total ? `${done}/${total}` : t("insp.parsingShort")}
          {eta ? <span className="eta">  ·  {t("insp.etaLeft", { eta: fmtEta(eta) })}</span> : null}
        </span>
      </div>
    </Frame>
  );
}
