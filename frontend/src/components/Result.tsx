import { useEffect, useState, type ReactNode } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { getResult, getRetained, getMessages, deleteJob, transcriptUrl, mediaUrl } from "../api";
import type { ReadResult, Retained, ReceiptMessage, ReceiptMedia } from "../types";
import Frame from "./Frame";
import { seedOf, thumb, wave } from "../lib/ascii";

// Stable left/right side per actor: distinct senders are ordered by first
// appearance and alternated, so each person sits consistently on one side across
// the whole read (a two-sided chat shape). Which side is "you" is a v2 concern —
// for now it's just the two actors, split.
function sidesOf(msgs: Record<number, ReceiptMessage>): Record<string, "me" | "them"> {
  const first: Record<string, number> = {};
  Object.values(msgs).forEach((m) => {
    if (first[m.sender] === undefined || m.id < first[m.sender]) first[m.sender] = m.id;
  });
  const map: Record<string, "me" | "them"> = {};
  Object.keys(first)
    .sort((a, b) => first[a] - first[b])
    .forEach((name, i) => (map[name] = i % 2 === 0 ? "them" : "me"));
  return map;
}

// A cited attachment: the REAL photo, served locally from the export (the evidence
// thesis made tangible — the actual image beside its blind caption). Falls back to
// the ASCII thumb if it can't render (a non-browser format, or the raw media was
// already deleted on the ephemeral path). Audio/video keep their ASCII glyph.
function BubbleMedia({ jobId, md }: { jobId: string; md: ReceiptMedia }) {
  const [failed, setFailed] = useState(false);
  const isPhoto = md.type === "image" || md.type === "sticker";
  return (
    <figure className="b-media">
      {isPhoto && jobId && !failed ? (
        <img
          className="b-photo"
          src={mediaUrl(jobId, md.file)}
          alt={md.caption || md.file}
          loading="lazy"
          onError={() => setFailed(true)}
        />
      ) : (
        <pre>{md.type === "audio" ? wave(seedOf(md.file), 16) : thumb(seedOf(md.file), 20, 6)}</pre>
      )}
      {md.caption && (
        <figcaption>
          “{md.caption}” <span className="b-blind">— blind caption</span>
        </figcaption>
      )}
    </figure>
  );
}

// The cited message(s), rendered inline as real chat bubbles — the one piece of
// styling the read carries. Actors split left/right (see sidesOf), so a quoted
// exchange reads the way it did in the app.
function ChatBubbles({ msgs, sides, jobId }: { msgs: ReceiptMessage[]; sides: Record<string, "me" | "them">; jobId: string }) {
  return (
    <div className="bubbles">
      {msgs.map((m) => (
        <div className={"bubble " + (sides[m.sender] ?? "them")} key={m.id}>
          <div className="b-meta">
            {m.sender} · {m.ts}
          </div>
          {m.text && <div className="b-text">{m.text}</div>}
          {m.media.map((md) => (
            <BubbleMedia key={md.file} jobId={jobId} md={md} />
          ))}
        </div>
      ))}
    </div>
  );
}

const PUNCT_ONLY = /^[.,;:!?…—\-\s]+$/;

// The read: flowing prose, with each [#id] citation (or a run of them) expanded
// in place into a chat-bubble cluster of the messages it points to. `##` lines,
// if the model emits any, degrade to a light subheading.
function renderRead(text: string, msgs: Record<number, ReceiptMessage>, jobId: string): ReactNode[] {
  const out: ReactNode[] = [];
  const sides = sidesOf(msgs);
  let firstProse = true;
  text.split(/\n\n+/).forEach((block, bi) => {
    const b = block.trim();
    if (!b) return;
    const head = b.match(/^#{2,}\s+(.*\S)\s*$/);
    if (head) {
      out.push(<h2 className="subhead" key={"h" + bi}>{head[1]}</h2>);
      return;
    }
    b.split(/((?:\s*\[#\d+\])+)/g).forEach((tok, ti) => {
      if (/\[#\d+\]/.test(tok)) {
        const ids = [...new Set([...tok.matchAll(/\[#(\d+)\]/g)].map((m) => Number(m[1])))];
        const cited = ids.map((id) => msgs[id]).filter(Boolean) as ReceiptMessage[];
        if (cited.length) out.push(<ChatBubbles key={"b" + bi + "_" + ti} msgs={cited} sides={sides} jobId={jobId} />);
      } else {
        const p = tok.trim();
        if (p && !PUNCT_ONLY.test(p)) {
          out.push(
            <p key={"p" + bi + "_" + ti} className={firstProse ? "lede" : ""}>
              {p}
            </p>
          );
          firstProse = false;
        }
      }
    });
  });
  return out;
}

export default function Result() {
  const { id } = useParams<{ id: string }>();
  const nav = useNavigate();
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
      "✓ nothing remains. starting over…",
    ];
    let i = 0;
    const acc: string[] = [];
    const tick = () => {
      acc.push(STEPS[i]);
      setReceipt([...acc]);
      i++;
      if (i < STEPS.length) window.setTimeout(tick, 400);
      // receipt finished — let the final line land, then return to the start.
      else window.setTimeout(() => nav("/"), 1800);
    };
    tick();
  }

  if (!res) {
    return (
      <Frame step="step 4/4 · the read" hero="loading the read">
        <div className="up">…</div>
      </Frame>
    );
  }

  return (
    <Frame
      step="step 4/4 · the read"
      hero="the read"
      top
      custody={nuked ? "✓ nothing remains" : "✓ raw media stays local · the read is yours to keep or destroy"}
    >
      <div className="read">
        {renderRead(res.read, msgs, id ?? "")}

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
