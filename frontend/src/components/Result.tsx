import { useEffect, useState, type ReactNode } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { getResult, getRetained, getMessages, deleteJob, transcriptUrl } from "../api";
import type { ReadResult, Retained, ReceiptMessage } from "../types";
import Frame from "./Frame";
import { useT } from "../lib/i18n";
import { Bubble, sidesOf } from "./Bubbles";
import ChatDrawer from "./ChatDrawer";

// H:MM:SS when over an hour, else M:SS — for the self-destruct countdown.
const fmtClock = (s: number) => {
  if (!isFinite(s) || s < 0) s = 0;
  s = Math.floor(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const p = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${p(m)}:${p(sec)}` : `${m}:${p(sec)}`;
};

// A cited message-cluster, rendered inline as real chat bubbles. Each bubble is
// clickable → the context drawer opens the full chat at that message.
function ChatBubbles({
  msgs,
  sides,
  jobId,
  onOpen,
}: {
  msgs: ReceiptMessage[];
  sides: Record<string, "me" | "them">;
  jobId: string;
  onOpen: (id: number) => void;
}) {
  return (
    <div className="bubbles">
      {msgs.map((m) => (
        <Bubble key={m.id} m={m} side={sides[m.sender] ?? "them"} jobId={jobId} onOpen={onOpen} />
      ))}
    </div>
  );
}

// Compact clickable citation chips — for LONG citation runs and for any id that
// didn't resolve to a fetched message (so a reference is NEVER dropped or leaked as
// raw text). A resolvable id opens the drawer; an unresolvable one is shown muted.
function CiteChips({
  ids,
  msgs,
  onOpen,
}: {
  ids: number[];
  msgs: Record<number, ReceiptMessage>;
  onOpen: (id: number) => void;
}) {
  const { t } = useT();
  return (
    <span className="cites">
      {ids.map((id) =>
        msgs[id] ? (
          <button key={id} className="cite" onClick={() => onOpen(id)}>
            #{id}
          </button>
        ) : (
          <span key={id} className="cite dead" title={t("drawer.notFound")}>
            #{id}
          </span>
        )
      )}
    </span>
  );
}

const PUNCT_ONLY = /^[.,;:!?…—\-\s]+$/;
const BUBBLE_CAP = 3; // runs up to this many ids stay inline bubbles; longer → chips
// A citation run: one or more [#id] brackets, back to back, tolerating multi-id /
// comma forms the model sometimes writes (e.g. [#12, #13]). Captured so split()
// keeps it as its own token.
const CITE_RUN = /((?:\s*\[#\s*\d+(?:\s*,\s*#?\s*\d+)*\s*\])+)/g;
const isCite = (tok: string) => /\[#\s*\d+/.test(tok);
const idsIn = (tok: string) => [...new Set((tok.match(/\d+/g) || []).map(Number))];

// The read: flowing prose. A SHORT citation run resolves to inline chat-bubble
// evidence; a LONG run — or any id that didn't resolve — becomes clickable chips
// (never dropped, never leaked as raw text). Bubbles and chips both open the
// context drawer at that message. `##` lines degrade to a light subheading.
function renderRead(
  text: string,
  msgs: Record<number, ReceiptMessage>,
  jobId: string,
  onOpen: (id: number) => void
): ReactNode[] {
  const out: ReactNode[] = [];
  const sides = sidesOf(Object.values(msgs));
  let firstProse = true;
  text.split(/\n\n+/).forEach((block, bi) => {
    const b = block.trim();
    if (!b) return;
    const head = b.match(/^#{2,}\s+(.*\S)\s*$/);
    if (head) {
      out.push(<h2 className="subhead" key={"h" + bi}>{head[1]}</h2>);
      return;
    }
    b.split(CITE_RUN).forEach((tok, ti) => {
      if (isCite(tok)) {
        const ids = idsIn(tok);
        const resolvable = ids.filter((id) => msgs[id]);
        const key = "c" + bi + "_" + ti;
        if (ids.length <= BUBBLE_CAP && resolvable.length > 0) {
          out.push(
            <ChatBubbles
              key={key}
              msgs={resolvable.map((id) => msgs[id])}
              sides={sides}
              jobId={jobId}
              onOpen={onOpen}
            />
          );
          const dead = ids.filter((id) => !msgs[id]);
          if (dead.length) out.push(<CiteChips key={key + "d"} ids={dead} msgs={msgs} onOpen={onOpen} />);
        } else {
          out.push(<CiteChips key={key} ids={ids} msgs={msgs} onOpen={onOpen} />);
        }
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
  const { t, tList } = useT();
  const [res, setRes] = useState<ReadResult | null>(null);
  const [retained, setRetained] = useState<Retained | null>(null);
  const [msgs, setMsgs] = useState<Record<number, ReceiptMessage>>({});
  const [nuked, setNuked] = useState(false);
  const [receipt, setReceipt] = useState<string[]>([]);
  const [remaining, setRemaining] = useState<number | null>(null);
  const [destroyed, setDestroyed] = useState(false);
  const [drawerFocus, setDrawerFocus] = useState<number | null>(null); // cited-message drawer

  // Self-destruct countdown: tick down to the read's expires_at (hosted tier).
  // At zero the read is gone server-side (the sweeper deletes it) — reflect that.
  useEffect(() => {
    const exp = res?.expires_at;
    if (!exp) {
      setRemaining(null);
      return;
    }
    const tick = () => {
      const left = exp - Date.now() / 1000;
      if (left <= 0) {
        setRemaining(0);
        setDestroyed(true);
      } else {
        setRemaining(left);
      }
    };
    tick();
    const iv = window.setInterval(tick, 1000);
    return () => window.clearInterval(iv);
  }, [res?.expires_at]);

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
    const STEPS = tList("result.nukeSteps");
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
      <Frame step={t("insp.step4")} hero={t("result.loadingHero")}>
        <div className="up">…</div>
      </Frame>
    );
  }

  if (destroyed && !nuked) {
    return (
      <Frame step={t("insp.step4")} hero={t("result.destroyedHero")}>
        <pre className="receipt">{tList("result.destroyedBody").join("\n")}</pre>
        <button className="nuke" onClick={() => nav("/")}>
          {t("result.startOver")}
          <small>{t("result.startOverSub")}</small>
        </button>
      </Frame>
    );
  }

  return (
    <Frame
      step={t("insp.step4")}
      hero={t("result.hero")}
      top
    >
      {remaining !== null && !nuked && (
        <div className={"selfdestruct" + (remaining <= 60 ? " urgent" : "")}>
          {t("result.selfDestructIn")} <strong>{fmtClock(remaining)}</strong>
          <small>{t("result.selfDestructSub")}</small>
        </div>
      )}

      <div className="read">
        {renderRead(res.read, msgs, id ?? "", setDrawerFocus)}

        {res.deep_count ? (
          <p className="prov">
            {res.deep_count === 1
              ? t("result.deepProv1", { n: res.deep_count })
              : t("result.deepProvN", { n: res.deep_count })}
          </p>
        ) : null}

        <div className="prov">
          {res.route
            ? t("result.readByRoute", { model: res.model || t("result.theModel"), route: res.route })
            : t("result.readByNoRoute", { model: res.model || t("result.theModel") })}
        </div>
      </div>

      <div className="provoke">{t("result.provoke")}</div>

      {nuked ? (
        <pre className="receipt">{receipt.join("\n")}</pre>
      ) : (
        <>
          {id && (
            <div className="sent-link">
              <a className="link" href={transcriptUrl(id)} target="_blank" rel="noreferrer">
                {t("result.viewText")}
              </a>
            </div>
          )}
          <div className="prov">
            {t("result.heldNow")}{" "}
            {[
              retained?.raw_media && t("result.heldRawMedia"),
              retained?.transcript && t("result.heldTranscript"),
              retained?.read && t("result.heldRead"),
            ]
              .filter(Boolean)
              .join(" · ") || t("result.heldNone")}
          </div>
          {/* TODO — SHARE BUTTON (PARKED; Konstantin undecided, 2026-06-23).
              Tension: a share affordance would hype the research / drive referral
              growth, but it cuts against the no-retention promise — sharing means a
              copy persists somewhere. Idea to revisit: turn it into a *punch* — a
              greyed-out / disabled "share" with a self-aware caption, e.g. "this
              would be great for our reach — but you're better off deleting it."
              Settle the framing with the narrative thread before building. */}
          <button className="nuke" onClick={doNuke}>
            {t("result.nukeBtn")}
            <small>{t("result.nukeSub")}</small>
          </button>
        </>
      )}
      <ChatDrawer jobId={id ?? ""} focusId={drawerFocus} onClose={() => setDrawerFocus(null)} />
    </Frame>
  );
}
