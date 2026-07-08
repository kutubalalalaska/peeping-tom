import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { uploadChat, getConfig } from "../api";
import { useT } from "../lib/i18n";
import Frame from "./Frame";

// Pull a human message out of a thrown api error ("429 {\"detail\":\"…\"}"). The
// backend detail is English, so for a rate-limit we show the localized copy;
// other (technical) errors surface raw.
function friendlyError(e: unknown, rateMsg: string): string {
  const s = String(e);
  return s.includes("429") ? rateMsg : s;
}

type Platform = "whatsapp" | "telegram";
type OS = "iphone" | "android";

// The Telegram JSON export exists ONLY in the desktop.telegram.org build (mobile
// can't export; the store apps do HTML at most, which this tool can't read) — the
// steps in the dictionary send people specifically to the desktop build.

export default function Start() {
  const [platform, setPlatform] = useState<Platform | null>(null);
  const [os, setOs] = useState<OS>("iphone");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [hosted, setHosted] = useState(false);
  const [agreed, setAgreed] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const nav = useNavigate();
  const { t, tList, lang } = useT();

  // Consent gate only matters on the hosted exhibit (you process others' uploads).
  useEffect(() => {
    getConfig().then((c) => setHosted(c.hosted)).catch(() => undefined);
  }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!file || !platform || (hosted && !agreed)) return;
    setErr(null);
    setBusy(true);
    try {
      const { job_id } = await uploadChat(file, platform, lang);
      nav(`/job/${job_id}`);
    } catch (e) {
      setErr(friendlyError(e, t("start.errRate")));
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
        </div>
      </Frame>
    );
  }

  const steps =
    platform === "telegram"
      ? tList("start.tg")
      : tList(os === "iphone" ? "start.wa.iphone" : "start.wa.android");

  return (
    <Frame
      step={t("start.step2")}
      hero={t("start.exportFrom", { platform })}
      nav={<button onClick={() => setPlatform(null)}>{t("common.back")}</button>}
    >
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
            <div>{t("start.thenDrop")}</div>
          </div>
        </div>
        <div className="ln" style={{ animationDelay: "160ms" }}>
          <input
            ref={inputRef}
            type="file"
            accept=".zip"
            style={{ display: "none" }}
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          <div className="row">
            <button type="button" className="opt" onClick={() => inputRef.current?.click()}>
              [ {file ? file.name.slice(0, 28) : t("start.chooseZip")} ]
            </button>
            <button
              type="submit"
              className={"opt solid" + (file && (!hosted || agreed) ? " ok" : "")}
              disabled={!file || busy || (hosted && !agreed)}
            >
              [ {busy ? t("start.uploading") : t("start.uploadBtn")} ]
            </button>
          </div>
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
