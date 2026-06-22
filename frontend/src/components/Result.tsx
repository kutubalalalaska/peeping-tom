import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import {
  getResult,
  getRetained,
  getMessages,
  deleteJob,
  transcriptUrl,
  mediaUrl,
} from "../api";
import type { ReadResult, Retained, ReceiptMessage } from "../types";

// The receipt drawer: the real cited message — text, and for media the raw image
// BESIDE its blind caption (the whole evidence thesis, made tangible).
function ReceiptPanel({
  jobId,
  msg,
  onClose,
}: {
  jobId: string;
  msg: ReceiptMessage | null;
  onClose: () => void;
}) {
  return (
    <div className="receipt-backdrop" onClick={onClose}>
      <aside className="receipt" onClick={(e) => e.stopPropagation()}>
        <button className="receipt-x" onClick={onClose} aria-label="close">
          ×
        </button>
        {!msg ? (
          <p className="muted">Couldn't load this message.</p>
        ) : (
          <>
            <div className="receipt-head">
              <b>{msg.sender}</b>{" "}
              <span className="muted small">
                {msg.ts} · #{msg.id}
              </span>
            </div>
            {msg.text && <p className="receipt-text">{msg.text}</p>}
            {msg.media.map((md) => (
              <figure className="receipt-media" key={md.file}>
                {md.type === "image" || md.type === "sticker" ? (
                  <img src={mediaUrl(jobId, md.file)} alt="" />
                ) : md.type === "audio" ? (
                  <audio controls src={mediaUrl(jobId, md.file)} />
                ) : md.type === "video" ? (
                  <video controls src={mediaUrl(jobId, md.file)} />
                ) : (
                  <span className="muted small">{md.file}</span>
                )}
                {md.caption && (
                  <figcaption className="muted small">
                    “{md.caption}”{" "}
                    <span className="receipt-blind">
                      — blind caption, written without seeing the chat
                    </span>
                  </figcaption>
                )}
              </figure>
            ))}
          </>
        )}
      </aside>
    </div>
  );
}

// Render the read, turning [#id] markers into clickable citation chips.
function renderRead(text: string, onCite: (id: number) => void) {
  return text.split(/(\[#\d+\])/g).map((part, i) => {
    const m = part.match(/^\[#(\d+)\]$/);
    if (m) {
      const cid = Number(m[1]);
      return (
        <button
          key={i}
          type="button"
          className="cite"
          onClick={() => onCite(cid)}
          title={`message #${cid}`}
        >
          #{m[1]}
        </button>
      );
    }
    return <span key={i}>{part}</span>;
  });
}

export default function Result() {
  const { id } = useParams<{ id: string }>();
  const [res, setRes] = useState<ReadResult | null>(null);
  const [retained, setRetained] = useState<Retained | null>(null);
  const [deleted, setDeleted] = useState(false);
  const [msgs, setMsgs] = useState<Record<number, ReceiptMessage>>({});
  const [openId, setOpenId] = useState<number | null>(null);

  useEffect(() => {
    if (!id) return;
    getRetained(id).then(setRetained).catch(() => undefined);
    getResult(id)
      .then((r) => {
        setRes(r);
        if (r.citations.length) {
          getMessages(id, r.citations)
            .then((list) =>
              setMsgs(Object.fromEntries(list.map((m) => [m.id, m])))
            )
            .catch(() => undefined);
        }
      })
      .catch(() => undefined);
  }, [id]);

  async function onDelete() {
    if (!id) return;
    await deleteJob(id);
    setDeleted(true);
    setOpenId(null);
    setMsgs({});
    setRetained({ raw_media: false, transcript: false, read: false });
  }

  if (!res) {
    return (
      <main className="wrap">
        <p className="muted">Loading the read…</p>
      </main>
    );
  }

  return (
    <main className="wrap">
      <h1>The read</h1>
      {res.model && (
        <p className="muted small prov">
          Read by {res.model}
          {res.route ? ` · via the ${res.route} route` : ""}. Only the text
          transcript crossed — nothing else left this machine.
        </p>
      )}
      <article className="read">{renderRead(res.read, setOpenId)}</article>

      <aside className="card retain">
        <b>What we hold on you right now</b>
        {deleted ? (
          <p className="ok">Nothing. Everything was deleted. ✓</p>
        ) : (
          <>
            <ul>
              <li>raw media: {retained?.raw_media ? "held" : "deleted"}</li>
              <li>transcript: {retained?.transcript ? "held" : "deleted"}</li>
              <li>the read: {retained?.read ? "held" : "—"}</li>
            </ul>
            {id && (
              <a href={transcriptUrl(id)} target="_blank" rel="noreferrer">
                view exactly what was sent
              </a>
            )}
            <button className="danger" onClick={onDelete}>
              Delete everything
            </button>
          </>
        )}
      </aside>

      {openId != null && (
        <ReceiptPanel
          jobId={id!}
          msg={msgs[openId] ?? null}
          onClose={() => setOpenId(null)}
        />
      )}
    </main>
  );
}
