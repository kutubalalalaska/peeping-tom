import { useEffect, useMemo, useRef, useState } from "react";
import { getAllMessages } from "../api";
import { useT } from "../lib/i18n";
import type { ReceiptMessage } from "../types";
import { Bubble, sidesOf } from "./Bubbles";

// The context drawer: the WHOLE chat, opened at a cited message and highlighted, so
// the reader can see any claim in situ and revise their own history. Slides in from
// the right (full-width on mobile). The full chat can be huge (100k+ messages), so
// the render is WINDOWED around the focus and grown on demand — never all at once.

const PAD = 40; // messages rendered each side of the focus to start
const STEP = 60; // how many more to reveal per earlier/later

export default function ChatDrawer({
  jobId,
  focusId,
  onClose,
}: {
  jobId: string;
  focusId: number | null;
  onClose: () => void;
}) {
  const { t } = useT();
  const open = focusId !== null;
  const [all, setAll] = useState<ReceiptMessage[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(false);
  const [win, setWin] = useState<[number, number]>([0, 0]);
  const focusRef = useRef<HTMLDivElement | null>(null);

  // Load the full chat ONCE, the first time the drawer opens. Cached across opens.
  useEffect(() => {
    if (!open || all || loading || err) return;
    setLoading(true);
    getAllMessages(jobId)
      .then((list) => {
        list.sort((a, b) => a.id - b.id);
        setAll(list);
      })
      .catch(() => setErr(true))
      .finally(() => setLoading(false));
  }, [open, jobId, all, loading, err]);

  const focusIdx = useMemo(
    () => (all && focusId != null ? all.findIndex((m) => m.id === focusId) : -1),
    [all, focusId]
  );

  // Re-center the window on the focused message when it changes (or once loaded).
  useEffect(() => {
    if (!all || focusIdx < 0) return;
    setWin([Math.max(0, focusIdx - PAD), Math.min(all.length, focusIdx + PAD + 1)]);
  }, [all, focusIdx]);

  // Scroll the focus into view — keyed on focusIdx only, so browsing earlier/later
  // (which changes `win`) never yanks the user back to the citation.
  useEffect(() => {
    if (focusIdx < 0) return;
    const id = window.setTimeout(() => focusRef.current?.scrollIntoView({ block: "center" }), 40);
    return () => window.clearTimeout(id);
  }, [focusIdx]);

  // Esc closes.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const sides = useMemo(() => (all ? sidesOf(all) : {}), [all]);
  const [start, end] = win;
  const slice = all ? all.slice(start, end) : [];

  return (
    <div
      className={"drawer-wrap" + (open ? " open" : "")}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      aria-hidden={!open}
    >
      <aside className="drawer" role="dialog" aria-modal="true">
        <div className="drawer-head">
          <span>{t("drawer.title")}</span>
          <button className="modalx" onClick={onClose}>
            [ {t("drawer.close")} ]
          </button>
        </div>
        <div className="drawer-body">
          {err ? (
            <div className="drawer-msg">{t("drawer.deleted")}</div>
          ) : !all ? (
            <div className="drawer-msg">{t("drawer.loading")}</div>
          ) : (
            <>
              {start > 0 && (
                <button className="drawer-more" onClick={() => setWin([Math.max(0, start - STEP), end])}>
                  {t("drawer.earlier")}
                </button>
              )}
              {slice.map((m, i) => {
                const isFocus = start + i === focusIdx;
                return (
                  <Bubble
                    key={m.id}
                    m={m}
                    side={sides[m.sender] ?? "them"}
                    jobId={jobId}
                    focused={isFocus}
                    refCb={isFocus ? (el) => (focusRef.current = el) : undefined}
                  />
                );
              })}
              {end < all.length && (
                <button className="drawer-more" onClick={() => setWin([start, Math.min(all.length, end + STEP)])}>
                  {t("drawer.later")}
                </button>
              )}
            </>
          )}
        </div>
      </aside>
    </div>
  );
}
