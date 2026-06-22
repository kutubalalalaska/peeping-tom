import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { getStatus, setRole, mediaUrl } from "../api";
import type { JobStatus, RecentItem } from "../types";

// A single fleeting glimpse of a just-decoded item — surfaces, then fades (CSS).
function Glimpse({ id, item }: { id: string; item: RecentItem }) {
  const t = item.type;
  return (
    <figure className="glimpse">
      {t === "image" || t === "sticker" ? (
        <img src={mediaUrl(id, item.file)} alt="" />
      ) : t === "audio" ? (
        <div className="g-audio">
          <audio controls src={mediaUrl(id, item.file)} />
        </div>
      ) : (
        <div className="g-video">▶</div>
      )}
      {item.caption && <figcaption>{item.caption}</figcaption>}
    </figure>
  );
}

// Decode + role pick. Continue hands off to the review gate (/review/:id), which
// is where anything actually crosses the boundary.
export default function Inspection() {
  const { id } = useParams<{ id: string }>();
  const [s, setS] = useState<JobStatus | null>(null);
  const [me, setMe] = useState<string | null>(null);
  const nav = useNavigate();

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
      if (st.state === "ready" || st.state === "error") clearInterval(t);
    }, 1000);
    return () => clearInterval(t);
  }, [id]);

  async function pick(name: string) {
    if (!id) return;
    setMe(name);
    await setRole(id, name).catch(() => undefined);
  }

  const state = s?.state;
  const pct = s?.progress?.pct ?? 0;
  const participants = s?.participants ?? [];
  const recent = (s?.recent ?? []).slice(-4);
  const decoding = state === "uploaded" || state === "inspecting";

  if (state === "error") {
    return (
      <main className="wrap">
        <h1>Something went wrong</h1>
        <p className="err">{s?.message}</p>
      </main>
    );
  }

  return (
    <main className="wrap">
      <h1>Inspecting locally</h1>

      <div className="stage">
        <div className="glimpses">
          {recent.map((it) => (
            <Glimpse key={it.file} id={id!} item={it} />
          ))}
        </div>
        <div className="meter">
          <div className="spinner" />
          <div className="pct">{pct}%</div>
        </div>
        <p className="muted small">
          Decoding your media on this machine — nothing has left it.
        </p>
      </div>

      <div className="card role">
        <b>Which one is you?</b>
        {participants.length === 0 ? (
          <p className="muted small">Reading the conversation…</p>
        ) : (
          <ul className="people">
            {participants.map((p) => (
              <li key={p.name}>
                <button
                  type="button"
                  className={me === p.name ? "on" : ""}
                  onClick={() => pick(p.name)}
                >
                  {p.name} <span className="muted">· {p.count}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <button onClick={() => id && nav(`/review/${id}`)} disabled={!me || decoding}>
        {decoding ? "Decoding…" : me ? "Continue" : "Pick yourself to continue"}
      </button>
    </main>
  );
}
