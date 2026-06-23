import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { getStatus, getConfig, setRole, sendRead } from "../api";
import type { JobStatus, RecentItem } from "../types";
import Frame from "./Frame";
import { SPIN, seedOf, thumb, wave, player, progBar } from "../lib/ascii";
import { useSpinFrame } from "../lib/hooks";

const tag = (t: string) =>
  t === "sticker" ? "stk" : t === "video" ? "vid" : t === "audio" ? "aud" : "img";

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

// Decode-as-inspection → select yourself → (send) → the read. One screen spans
// uploaded → inspecting → ready → analyzing, then navigates to /result on done.
export default function Inspection() {
  const { id } = useParams<{ id: string }>();
  const [s, setS] = useState<JobStatus | null>(null);
  const [sending, setSending] = useState(false);
  const [routeId, setRouteId] = useState<string | undefined>(undefined);
  const nav = useNavigate();
  const sf = useSpinFrame(true);

  useEffect(() => {
    getConfig()
      .then((c) => setRouteId(c.default_route ?? c.routes?.find((r) => r.ready)?.id))
      .catch(() => undefined);
  }, []);

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

  async function pick(name: string) {
    if (!id) return;
    setSending(true);
    await setRole(id, name).catch(() => undefined);
    await sendRead(id, routeId).catch(() => undefined);
  }

  const state = s?.state;
  const pct = s?.progress?.pct ?? 0;
  const done = s?.progress?.done ?? 0;
  const total = s?.progress?.total ?? 0;
  const recent = s?.recent ?? [];
  const latest = recent[recent.length - 1];
  const spin = SPIN[sf % SPIN.length];

  if (state === "error") {
    return (
      <Frame step="error" hero="something broke">
        <p className="err">{s?.message}</p>
      </Frame>
    );
  }
  if (state === "needs_config") {
    return (
      <Frame step="config" hero="no read route">
        <p className="err">{s?.message}</p>
        <p className="hint2">set a read route (or FRONTIER_PROVIDER=mock) and retry.</p>
      </Frame>
    );
  }

  // sending (optimistic) or analyzing → the read is being generated
  if (sending || state === "analyzing") {
    return (
      <Frame
        step="step 5/5 · the read"
        hero="reading your chat"
        custody="✓ raw media stays local · only the transcript crossed"
      >
        <div className="pcontent">
          <div className="up">
            {s?.message ?? "the model is reading the transcript…"}
            <br />
            only the text crossed — nothing else left this machine
          </div>
        </div>
        <div className="barrow">
          <span className="pre">{progBar(100)}</span>
          <span className="phase">{spin}&nbsp;&nbsp;{s?.message ?? "the read…"}</span>
        </div>
      </Frame>
    );
  }

  // ready → select yourself (picking yourself sends the transcript)
  if (state === "ready") {
    const participants = s?.participants ?? [];
    return (
      <Frame
        step="step 4/5 · you"
        hero="select yourself"
        custody="nothing has left this machine yet"
      >
        <div className="ln">
          <div className="hint2">
            one question — it anchors the whole read to you. picking yourself sends
            only the transcript to the model.
          </div>
        </div>
        <div className="ln" style={{ animationDelay: "80ms" }}>
          <div className="row">
            {participants.map((p) => (
              <button key={p.name} className="opt" disabled={sending} onClick={() => pick(p.name)}>
                [ {p.name} · {p.count} ]
              </button>
            ))}
          </div>
        </div>
      </Frame>
    );
  }

  // uploaded / inspecting → processing
  const uploading = state === "uploaded" || !state;
  return (
    <Frame
      step={uploading ? "step 3/5 · upload" : "step 3/5 · decode"}
      hero={uploading ? "uploading your chat" : "decoding your media"}
      custody="decoded on this machine — nothing has left it"
    >
      <div className="pcontent">
        {uploading ? (
          <div className="up">
            sending chat.zip to this machine…
            <br />
            the raw file stays local
          </div>
        ) : (
          <>
            <div className="stage">
              {latest ? <Glimpse item={latest} sf={sf} /> : <div className="up">parsing…</div>}
            </div>
            <div className="tail">
              <div className="lab">just decoded</div>
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
          </>
        )}
      </div>
      <div className="barrow">
        <span className="pre">{progBar(pct)}</span>
        <span className="phase">
          {spin}&nbsp;&nbsp;{uploading ? "uploading…" : `decode media  ${done}/${total}`}
        </span>
      </div>
    </Frame>
  );
}
