import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { uploadChatChunked, getConfig } from "../api";
import { useT } from "../lib/i18n";
import { detectOS, isMobileOS } from "../lib/platform";
import { progBar } from "../lib/ascii";
import { fmtEta } from "../lib/fmt";
import { ExportFormatError, ONE_PASS_TOKENS, openExport, rangeTokens, type SliceMeta } from "../lib/slicer";
import Frame from "./Frame";
import Slicer from "./Slicer";

// Pull a human message out of a thrown api error ("429 {\"detail\":\"…\"}"). The
// backend detail is English, so for the guard cases we show localized copy; other
// (technical) errors surface raw.
function friendlyError(e: unknown, rateMsg: string, tooLargeMsg: string): string {
  const s = String(e);
  if (s.includes("429")) return rateMsg;
  if (s.includes("413")) return tooLargeMsg;
  return s;
}

type Platform = "whatsapp" | "telegram";
type OS = "iphone" | "android";
type ReadMode = "fast" | "deep";

// The Telegram JSON export exists ONLY in the desktop.telegram.org build (mobile
// can't export; the store apps do HTML at most, which this tool can't read) — the
// steps in the dictionary send people specifically to the desktop build.

export default function Start() {
  // Detect the CURRENT device once — it drives the export guidance (the .zip
  // handoff differs per device, and Telegram can't export on a phone at all).
  const [detectedOS] = useState(detectOS);
  const onMobile = isMobileOS(detectedOS);
  const [platform, setPlatform] = useState<Platform | null>(null);
  const [os, setOs] = useState<OS>(detectedOS === "android" ? "android" : "iphone");
  const [mode, setMode] = useState<ReadMode>("fast");
  const [file, setFile] = useState<File | null>(null);
  const [oversize, setOversize] = useState<File | null>(null);      // over-cap zip → local slicer
  const [sliceMeta, setSliceMeta] = useState<SliceMeta | null>(null); // honest provenance of a slice
  const [busy, setBusy] = useState(false);
  const [pct, setPct] = useState(0);
  const [upEta, setUpEta] = useState<number | null>(null);
  const [bgPaused, setBgPaused] = useState(false);   // a background pause happened this upload
  // ETA baseline: rate is measured from the last RESUME, not from upload start —
  // a background pause (Safari suspends hidden tabs) would otherwise poison it.
  const upBase = useRef({ t: 0, received: 0 });
  const lastReceived = useRef(0);
  const busyRef = useRef(false);
  const [err, setErr] = useState<string | null>(null);
  const [hosted, setHosted] = useState(false);
  const [capMB, setCapMB] = useState(0);
  const [agreed, setAgreed] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const nav = useNavigate();
  const { t, tList, lang } = useT();

  // Consent gate only matters on the hosted exhibit (you process others' uploads).
  // max_upload_mb powers the over-cap detection BEFORE any byte is sent.
  useEffect(() => {
    getConfig()
      .then((c) => {
        setHosted(c.hosted);
        setCapMB(c.max_upload_mb ?? 0);
      })
      .catch(() => undefined);
  }, []);

  async function pickFile(f: File | null) {
    setErr(null);
    setSliceMeta(null);
    if (!f) {
      setOversize(null);
      setFile(null);
      return;
    }
    // Two gates, both checked BEFORE any byte uploads: the byte cap, and the
    // one-pass read window (a 30MB zip can still hold 100k messages of text —
    // size alone can't catch that). Either overflow → offer the local slicer.
    let over = !!capMB && f.size > capMB * 1024 * 1024;
    if (!over && platform) {
      try {
        const m = await openExport(f, platform);
        over = rangeTokens(m, 0, m.msgs.length) > ONE_PASS_TOKENS;
      } catch (ex) {
        // A readable zip that just isn't this platform's export is rejected HERE,
        // before a single byte uploads. Anything else stays fail-open — the
        // server's parser gets its own try.
        if (ex instanceof ExportFormatError) {
          setErr(ex.found && ex.found !== platform
            ? t("start.errWrongPlatform", { found: ex.found, selected: platform })
            : t("start.errNotExport", { platform }));
          setOversize(null);
          setFile(null);
          return;
        }
      }
    }
    if (over) {
      setFile(null);
      setOversize(f);
      return;
    }
    setOversize(null);
    setFile(f);
  }

  // Abort an in-flight upload if the user navigates away mid-transfer.
  useEffect(() => () => abortRef.current?.abort(), []);

  // Mid-upload guards: Safari suspends background tabs (the chunk chain stalls
  // until the tab returns — resumable, but it LOOKS hung), so (a) surface an
  // honest hint once a pause happened, (b) restart the ETA rate window on wake,
  // (c) warn before closing the tab while an upload is in flight.
  useEffect(() => {
    const onVis = () => {
      if (!busyRef.current) return;
      if (document.hidden) {
        setBgPaused(true);
        setUpEta(null);
      } else {
        upBase.current = { t: Date.now(), received: lastReceived.current };
      }
    };
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      if (!busyRef.current) return;
      e.preventDefault();
      e.returnValue = "";
    };
    document.addEventListener("visibilitychange", onVis);
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => {
      document.removeEventListener("visibilitychange", onVis);
      window.removeEventListener("beforeunload", onBeforeUnload);
    };
  }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!file || !platform || (hosted && !agreed)) return;
    setErr(null);
    setBusy(true);
    busyRef.current = true;
    setBgPaused(false);
    setPct(0);
    setUpEta(null);
    upBase.current = { t: Date.now(), received: 0 };
    lastReceived.current = 0;
    abortRef.current = new AbortController();
    try {
      // Resumable chunked upload: shows real progress and survives a dropped
      // connection (retry from the server's byte offset). See mirror/uploads.py.
      const { job_id } = await uploadChatChunked(file, platform, lang, mode, {
        signal: abortRef.current.signal,
        onProgress: (received, total) => {
          lastReceived.current = received;
          setPct(total ? Math.round((received / total) * 100) : 0);
          // Self-correcting ETA over the CURRENT rate window (reset on tab wake).
          // Held back for the first seconds — one chunk is noise, not an estimate.
          const base = upBase.current;
          const elapsed = (Date.now() - base.t) / 1000;
          const gained = received - base.received;
          setUpEta(elapsed > 2 && gained > 0 && received < total
            ? (total - received) / (gained / elapsed)
            : null);
        },
        sliceMeta: sliceMeta ?? undefined,
      });
      busyRef.current = false;
      nav(`/job/${job_id}`);
    } catch (e) {
      busyRef.current = false;
      if ((e as Error)?.name === "AbortError") return; // navigated away — nothing to show
      setErr(friendlyError(e, t("start.errRate"), t("start.errTooLarge")));
      setBusy(false);
    }
  }

  if (!platform) {
    return (
      <Frame
        step={t("start.step1")}
        hero={t("start.selectPlatform")}
        nav={<button onClick={() => nav("/")}>{t("common.back")}</button>}
      >
        <div className="ln">
          <div className="row">
            <button className="opt" onClick={() => setPlatform("whatsapp")}>
              [ whatsapp ]
            </button>
            <button className="opt" onClick={() => setPlatform("telegram")}>
              [ telegram ]
            </button>
          </div>
          {onMobile && <div className="mobilehint">{t("start.mobileHint")}</div>}
        </div>
      </Frame>
    );
  }

  const steps =
    platform === "telegram"
      ? tList("start.tg")
      : tList(os === "iphone" ? "start.wa.iphone" : "start.wa.android");
  // Device-aware .zip handoff. Telegram export is desktop-only, so its zip is made
  // right here → the generic "drop it below". WhatsApp is exported on a phone, so the
  // line explains getting that .zip into THIS device (same phone, or a computer).
  const handoffKey =
    platform === "telegram"
      ? "start.thenDrop"
      : detectedOS === "ios"
      ? "start.handoff.ios"
      : detectedOS === "android"
      ? "start.handoff.android"
      : "start.handoff.desktop";
  const tgMobile = platform === "telegram" && onMobile;

  return (
    <Frame
      step={t("start.step2")}
      hero={t("start.exportFrom", { platform })}
      nav={<button onClick={() => setPlatform(null)}>{t("common.back")}</button>}
    >
      {tgMobile && (
        <div className="ln">
          <div className="notice">
            <strong>{t("start.tgMobile.title")}</strong>
            {t("start.tgMobile.body")}
          </div>
        </div>
      )}
      <form onSubmit={submit}>
        {platform === "whatsapp" && (
          <div className="ln">
            <div className="row">
              <button
                type="button"
                className={"opt" + (os === "iphone" ? " sel" : "")}
                onClick={() => setOs("iphone")}
              >
                [ iphone ]
              </button>
              <button
                type="button"
                className={"opt" + (os === "android" ? " sel" : "")}
                onClick={() => setOs("android")}
              >
                [ android ]
              </button>
            </div>
          </div>
        )}
        <div className="ln" style={{ animationDelay: "80ms" }}>
          <div className="steps">
            {steps.map((st, i) => (
              <div key={i}>
                {i + 1} · {st}
              </div>
            ))}
            <div>{t(handoffKey)}</div>
          </div>
        </div>
        <div className="ln" style={{ animationDelay: "120ms" }}>
          {/* The read mode: fast = text-first, the model requests only the media it
              needs; deep = decode everything in parallel with the read. */}
          <div className="row">
            <button
              type="button"
              className={"opt" + (mode === "fast" ? " sel" : "")}
              onClick={() => setMode("fast")}
            >
              {t("start.mode.fast")}
            </button>
            <button
              type="button"
              className={"opt" + (mode === "deep" ? " sel" : "")}
              onClick={() => setMode("deep")}
            >
              {t("start.mode.deep")}
            </button>
          </div>
          <div className="modesub">
            {t(mode === "fast" ? "start.mode.fastSub" : "start.mode.deepSub")}
          </div>
        </div>
        <div className="ln" style={{ animationDelay: "160ms" }}>
          <input
            ref={inputRef}
            type="file"
            accept=".zip"
            style={{ display: "none" }}
            onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
          />
          {oversize && platform && (
            <Slicer
              file={oversize}
              source={platform}
              capMB={capMB}
              onReady={(sliced, meta) => {
                setOversize(null);
                setSliceMeta(meta);
                setFile(sliced);
              }}
              onCancel={() => {
                setOversize(null);
                if (inputRef.current) inputRef.current.value = "";
              }}
            />
          )}
          <div className="row">
            <button type="button" className="opt" onClick={() => inputRef.current?.click()}>
              [ {file ? (sliceMeta ? `${t("slice.slicedName")} ${sliceMeta.range}` : file.name.slice(0, 28)) : t("start.chooseZip")} ]
            </button>
            <button
              type="submit"
              className={"opt solid" + (file && (!hosted || agreed) ? " ok" : "")}
              disabled={!file || busy || (hosted && !agreed)}
            >
              [ {busy ? `${t("start.uploading")} ${pct}%` : t("start.uploadBtn")} ]
            </button>
          </div>
          {busy && (
            <>
              <pre className="uppre">
                {progBar(pct)}
                {upEta != null && (
                  <span className="eta">{`  ·  ${t("insp.etaLeft", { eta: fmtEta(upEta) })}`}</span>
                )}
              </pre>
              {bgPaused && <div className="bgbar">{t("start.bgPause")}</div>}
            </>
          )}
          {hosted && (
            <label className="consent">
              <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />
              <span>{t("start.consent")}</span>
            </label>
          )}
        </div>
        {err && <p className="err">{err}</p>}
      </form>
    </Frame>
  );
}
