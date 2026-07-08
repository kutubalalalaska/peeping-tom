import { useRef, useState } from "react";
import { mediaUrl } from "../api";
import { seedOf, thumb, wave } from "../lib/ascii";
import { useT } from "../lib/i18n";
import type { ReceiptMessage, ReceiptMedia } from "../types";

// Shared chat-bubble rendering, used by BOTH the read (inline evidence) and the
// context drawer (the full chat). Extracted from Result so there's one source.

// Stable left/right side per actor: distinct senders ordered by first appearance
// and alternated, so each person sits consistently on one side. Which side is
// "you" is a v2 concern — for now it's just the actors, split.
export function sidesOf(list: ReceiptMessage[]): Record<string, "me" | "them"> {
  const first: Record<string, number> = {};
  list.forEach((m) => {
    if (first[m.sender] === undefined || m.id < first[m.sender]) first[m.sender] = m.id;
  });
  const map: Record<string, "me" | "them"> = {};
  Object.keys(first)
    .sort((a, b) => first[a] - first[b])
    .forEach((name, i) => (map[name] = i % 2 === 0 ? "them" : "me"));
  return map;
}

const fmt = (s: number) => {
  if (!isFinite(s) || s < 0) s = 0;
  return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
};

// The REAL cited photo, served locally from the export — the actual image beside
// the caption the model wrote blind. Falls back to the ASCII thumb if it can't
// render (non-browser format, or the raw media already deleted on the ephemeral path).
function Photo({ jobId, md }: { jobId: string; md: ReceiptMedia }) {
  const [failed, setFailed] = useState(false);
  if (!jobId || failed) return <pre>{thumb(seedOf(md.file), 20, 6)}</pre>;
  return (
    <img
      className="b-photo"
      src={mediaUrl(jobId, md.file)}
      alt={md.caption || md.file}
      loading="lazy"
      onError={() => setFailed(true)}
    />
  );
}

// A cited voice note, actually playable — a mono play/pause transport over the real
// audio, with a live ▮▯ bar. stopPropagation so a tap on play never bubbles up to a
// clickable bubble (which would open the drawer). Falls back to the static waveform.
function AudioBit({ jobId, md }: { jobId: string; md: ReceiptMedia }) {
  const ref = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [failed, setFailed] = useState(false);
  const [t, setT] = useState(0);
  const [dur, setDur] = useState(0);
  if (!jobId) return <pre>{wave(seedOf(md.file), 16)}</pre>;
  const filled = dur > 0 ? Math.round(Math.min(1, t / dur) * 12) : 0;
  const bar = "▮".repeat(filled) + "▯".repeat(12 - filled);
  const toggle = () => {
    const a = ref.current;
    if (!a) return;
    if (a.paused) a.play().catch(() => setFailed(true));
    else a.pause();
  };
  return (
    <div className="b-audio">
      {failed ? (
        <pre>{wave(seedOf(md.file), 16)}</pre>
      ) : (
        <button
          className="audio-btn"
          onClick={(e) => {
            e.stopPropagation();
            toggle();
          }}
          aria-label={playing ? "pause" : "play"}
        >
          <span className="ap-ico">{playing ? "⏸" : "▶"}</span>
          <span className="ap-bar">{bar}</span>
          <span className="ap-time">
            {fmt(t)} / {fmt(dur)}
          </span>
        </button>
      )}
      <audio
        ref={ref}
        src={mediaUrl(jobId, md.file)}
        preload="metadata"
        onLoadedMetadata={(e) => setDur(e.currentTarget.duration)}
        onTimeUpdate={(e) => setT(e.currentTarget.currentTime)}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onError={() => setFailed(true)}
      />
    </div>
  );
}

// A cited attachment as real evidence: the photo, a playable voice note, or an ASCII
// glyph for other types. The blind caption rides underneath.
function BubbleMedia({ jobId, md }: { jobId: string; md: ReceiptMedia }) {
  const { t } = useT();
  const isPhoto = md.type === "image" || md.type === "sticker";
  return (
    <figure className="b-media">
      {isPhoto ? (
        <Photo jobId={jobId} md={md} />
      ) : md.type === "audio" ? (
        <AudioBit jobId={jobId} md={md} />
      ) : (
        <pre>{thumb(seedOf(md.file), 20, 6)}</pre>
      )}
      {md.caption && (
        <figcaption>
          “{md.caption}” <span className="b-blind">{t("bubble.blindCaption")}</span>
        </figcaption>
      )}
    </figure>
  );
}

// One chat bubble. Clickable (opens the context drawer at this message) when onOpen
// is given — the read uses that; the drawer itself renders bubbles non-clickable and
// flags the focused one. refCb lets the drawer scroll the focused bubble into view.
export function Bubble({
  m,
  side,
  jobId,
  onOpen,
  focused,
  refCb,
}: {
  m: ReceiptMessage;
  side: "me" | "them";
  jobId: string;
  onOpen?: (id: number) => void;
  focused?: boolean;
  refCb?: (el: HTMLDivElement | null) => void;
}) {
  const clickable = !!onOpen;
  const { t } = useT();
  return (
    <div
      ref={refCb}
      className={"bubble " + side + (focused ? " focus" : "") + (clickable ? " clickable" : "")}
      onClick={clickable ? () => onOpen!(m.id) : undefined}
      role={clickable ? "button" : undefined}
      tabIndex={clickable ? 0 : undefined}
      onKeyDown={
        clickable
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onOpen!(m.id);
              }
            }
          : undefined
      }
    >
      <div className="b-meta">
        {m.sender} · {m.ts}
        {clickable && <span className="b-open"> · {t("bubble.openInChat")} ↗</span>}
      </div>
      {m.text && <div className="b-text">{m.text}</div>}
      {m.media.map((md) => (
        <BubbleMedia key={md.file} jobId={jobId} md={md} />
      ))}
    </div>
  );
}
