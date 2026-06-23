import { useEffect, useState, type ReactNode } from "react";
import { useParams } from "react-router-dom";
import { getResult, getRetained, getMessages, deleteJob, transcriptUrl } from "../api";
import type { ReadResult, Retained, ReceiptMessage } from "../types";
import Frame from "./Frame";
import { seedOf, thumb, wave } from "../lib/ascii";

// A cited message (or a small run of them) rendered as a centered, styled block
// inserted into the read body — the claim, then the receipt, in line.
function QuoteBlock({ msgs, me }: { msgs: ReceiptMessage[]; me: string }) {
  return (
    <div className="quote">
      {msgs.map((m) => (
        <div className="q-msg" key={m.id}>
          <div className="q-meta">
            {m.sender} · {m.ts} · #{m.id}
          </div>
          {m.text && <div className={"q-line" + (m.sender === me ? " you" : "")}>{m.text}</div>}
          {m.media.map((md) => (
            <figure className="q-media" key={md.file}>
              <pre>{md.type === "audio" ? wave(seedOf(md.file), 16) : thumb(seedOf(md.file), 22, 7)}</pre>
              {md.caption && (
                <figcaption>
                  “{md.caption}” <span className="q-blind">— blind caption</span>
                </figcaption>
              )}
            </figure>
          ))}
        </div>
      ))}
    </div>
  );
}

const PUNCT_ONLY = /^[.,;:!?…—\-\s]+$/;

// Prose + inline citation quote-blocks for one section's body.
function renderBody(
  body: string,
  msgs: Record<number, ReceiptMessage>,
  me: string,
  teaser: boolean,
  kp: string
): ReactNode[] {
  const out: ReactNode[] = [];
  body.split(/((?:\s*\[#\d+\])+)/g).forEach((tok, ti) => {
    const isCite = /^(?:\s*\[#\d+\])+$/.test(tok) && tok.trim() !== "";
    if (isCite) {
      const ids = [...new Set([...tok.matchAll(/\[#(\d+)\]/g)].map((m) => Number(m[1])))];
      const cited = ids.map((id) => msgs[id]).filter(Boolean) as ReceiptMessage[];
      if (cited.length) out.push(<QuoteBlock key={kp + "q" + ti} msgs={cited} me={me} />);
    } else {
      tok.split(/\n\n+/).forEach((para, pi) => {
        const p = para.trim();
        if (!p || PUNCT_ONLY.test(p)) return;
        out.push(
          <p key={kp + "p" + ti + "_" + pi} className={teaser ? "teaser" : ""}>
            {p}
          </p>
        );
      });
    }
  });
  return out;
}

// The read as a reading: a teaser, then `## chapter` sections, each flowing prose
// with its cited messages inline, ending on the model's stated limits.
function renderRead(text: string, msgs: Record<number, ReceiptMessage>, me: string): ReactNode[] {
  const sections: { title: string | null; body: string }[] = [{ title: null, body: "" }];
  text.split("\n").forEach((line) => {
    const h = line.match(/^\s*##\s+(.*\S)\s*$/);
    if (h) sections.push({ title: h[1].trim(), body: "" });
    else sections[sections.length - 1].body += line + "\n";
  });
  const out: ReactNode[] = [];
  sections.forEach((sec, si) => {
    if (sec.title) out.push(<h2 className="chapter" key={"h" + si}>{sec.title}</h2>);
    out.push(...renderBody(sec.body, msgs, me, si === 0 && !sec.title, "s" + si));
  });
  return out;
}

export default function Result() {
  const { id } = useParams<{ id: string }>();
  const [res, setRes] = useState<ReadResult | null>(null);
  const [retained, setRetained] = useState<Retained | null>(null);
  const [msgs, setMsgs] = useState<Record<number, ReceiptMessage>>({});
  const [nuked, setNuked] = useState(false);
  const [receipt, setReceipt] = useState<string[]>([]);

  useEffect(() => {
    if (!id) return;
    getRetained(id).then(setRetained).catch(() => undefined);
    getResult(id)
      .then((r) => {
        setRes(r);
        if (r.citations.length) {
          getMessages(id, r.citations)
            .then((list) => setMsgs(Object.fromEntries(list.map((m) => [m.id, m]))))
            .catch(() => undefined);
        }
      })
      .catch(() => undefined);
  }, [id]);

  function doNuke() {
    if (!id) return;
    deleteJob(id).catch(() => undefined);
    setNuked(true);
    setRetained({ raw_media: false, transcript: false, read: false });
    const STEPS = [
      "> nuke --all",
      "purging raw media ........ gone",
      "purging transcript ....... gone",
      "purging the read ......... gone",
      "purging this session ..... gone",
      "",
      "✓ nothing remains. you can close the tab.",
    ];
    let i = 0;
    const acc: string[] = [];
    const tick = () => {
      acc.push(STEPS[i]);
      setReceipt([...acc]);
      i++;
      if (i < STEPS.length) window.setTimeout(tick, 400);
    };
    tick();
  }

  if (!res) {
    return (
      <Frame step="step 5/5 · the read" hero="loading the read">
        <div className="up">…</div>
      </Frame>
    );
  }

  return (
    <Frame
      step="step 5/5 · the read"
      hero="the read"
      top
      custody={nuked ? "✓ nothing remains" : "✓ raw media stays local · the read is yours to keep or destroy"}
    >
      <div className="read">
        {renderRead(res.read, msgs, res.me)}

        {res.deep_count ? (
          <p className="prov">
            the model asked for a closer look at {res.deep_count} photo
            {res.deep_count > 1 ? "s" : ""}, then re-read with them in view.
          </p>
        ) : null}

        <div className="prov">
          read by {res.model || "the model"}
          {res.route ? ` · via the ${res.route} route` : ""} — only the text transcript crossed.
        </div>
      </div>

      <div className="provoke">this is how a frontier ai model profiled you — for good, or for bad.</div>

      {nuked ? (
        <pre className="receipt">{receipt.join("\n")}</pre>
      ) : (
        <>
          {id && (
            <div className="sent-link">
              <a className="link" href={transcriptUrl(id)} target="_blank" rel="noreferrer">
                view the exact text that was sent →
              </a>
            </div>
          )}
          <div className="prov">
            held now: {retained?.raw_media ? "raw media · " : ""}
            {retained?.transcript ? "transcript · " : ""}
            {retained?.read ? "the read" : "—"}
          </div>
          {/* TODO — SHARE BUTTON (PARKED; Konstantin undecided, 2026-06-23).
              Tension: a share affordance would hype the research / drive referral
              growth, but it cuts against the no-retention promise — sharing means a
              copy persists somewhere. Idea to revisit: turn it into a *punch* — a
              greyed-out / disabled "share" with a self-aware caption, e.g. "this
              would be great for our reach — but you're better off deleting it."
              Settle the framing with the narrative thread before building. */}
          <button className="nuke" onClick={doNuke}>
            nuke all my data
            <small>deletes the transcript, the read, everything — no copy is kept</small>
          </button>
        </>
      )}
    </Frame>
  );
}
