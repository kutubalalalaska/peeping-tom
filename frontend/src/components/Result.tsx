import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { getResult, getRetained, getMessages, deleteJob, transcriptUrl } from "../api";
import type { ReadResult, Retained, ReceiptMessage } from "../types";
import Frame from "./Frame";
import { seedOf, thumb, wave } from "../lib/ascii";

// Turn [#id] markers into inverted citation chips.
function renderPara(text: string, me: string, onCite: (id: number) => void) {
  void me;
  return text.split(/(\[#\d+\])/g).map((part, i) => {
    const m = part.match(/^\[#(\d+)\]$/);
    if (m) {
      const cid = Number(m[1]);
      return (
        <button key={i} className="cite" onClick={() => onCite(cid)}>
          #{m[1]}
        </button>
      );
    }
    return <span key={i}>{part}</span>;
  });
}

// The cited message, revealed when a chip is clicked — text + ASCII media + its
// blind caption (the evidence, made legible).
function Receipt({ msg, me }: { msg: ReceiptMessage; me: string }) {
  const isMe = msg.sender === me;
  return (
    <div className="excerpt flash">
      <div className="x-meta">
        {msg.ts} · #{msg.id}
      </div>
      <div className={"x-line" + (isMe ? " you" : "")}>
        <span className="who">{msg.sender}</span>
        {msg.text}
      </div>
      {msg.media.map((md) => (
        <div className="rmedia" key={md.file}>
          <pre>{md.type === "audio" ? wave(seedOf(md.file), 16) : thumb(seedOf(md.file), 20, 7)}</pre>
          <div>
            <div className="m-tag">
              [{md.type}] {md.file}
            </div>
            {md.caption && <div className="m-cap">“{md.caption}”</div>}
            {md.caption && (
              <div className="m-blind">— blind caption, written without seeing the chat</div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

const NUKE_STEPS = [
  "> nuke --all",
  "purging raw media ........ gone",
  "purging transcript ....... gone",
  "purging the read ......... gone",
  "purging this session ..... gone",
  "",
  "✓ nothing remains. you can close the tab.",
];

export default function Result() {
  const { id } = useParams<{ id: string }>();
  const [res, setRes] = useState<ReadResult | null>(null);
  const [retained, setRetained] = useState<Retained | null>(null);
  const [msgs, setMsgs] = useState<Record<number, ReceiptMessage>>({});
  const [openId, setOpenId] = useState<number | null>(null);
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

  async function onCite(cid: number) {
    setOpenId(cid);
    if (!msgs[cid] && id) {
      const list = await getMessages(id, [cid]).catch(() => []);
      if (list.length) setMsgs((m) => ({ ...m, [cid]: list[0] }));
    }
  }

  function doNuke() {
    if (!id) return;
    deleteJob(id).catch(() => undefined);
    setNuked(true);
    setRetained({ raw_media: false, transcript: false, read: false });
    let i = 0;
    const acc: string[] = [];
    const tick = () => {
      acc.push(NUKE_STEPS[i]);
      setReceipt([...acc]);
      i++;
      if (i < NUKE_STEPS.length) window.setTimeout(tick, 400);
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

  const me = res.me;
  const paras = res.read.split(/\n\n+/).filter((p) => p.trim());

  return (
    <Frame
      step="step 5/5 · the read"
      hero="the read"
      top
      custody={
        nuked
          ? "✓ nothing remains"
          : "✓ raw media stays local · the read is yours to keep or destroy"
      }
    >
      <div className="read">
        {paras.map((p, i) => (
          <p key={i} className={i === 0 ? "lede" : ""}>
            {renderPara(p, me, onCite)}
          </p>
        ))}

        {res.deep_count ? (
          <p className="prov">
            the model asked for a closer look at {res.deep_count} photo
            {res.deep_count > 1 ? "s" : ""}, then re-read with them in view.
          </p>
        ) : null}

        {openId != null && msgs[openId] && <Receipt key={openId} msg={msgs[openId]} me={me} />}

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
          <button className="nuke" onClick={doNuke}>
            nuke all my data
            <small>deletes the transcript, the read, everything — no copy is kept</small>
          </button>
        </>
      )}
    </Frame>
  );
}
