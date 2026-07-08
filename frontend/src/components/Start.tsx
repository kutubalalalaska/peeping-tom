import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { uploadChat, getConfig } from "../api";
import Frame from "./Frame";

// Pull a human message out of a thrown api error ("429 {\"detail\":\"…\"}").
function friendlyError(e: unknown): string {
  const s = String(e);
  if (s.includes("429")) {
    const m = s.match(/\{.*\}/);
    if (m) {
      try {
        const d = JSON.parse(m[0]).detail;
        if (d) return d;
      } catch {
        /* fall through */
      }
    }
    return "You've reached your reads for now. Try again later.";
  }
  return s;
}

type Platform = "whatsapp" | "telegram";
type OS = "iphone" | "android";

const WA_STEPS: Record<OS, string[]> = {
  iphone: [
    "open the chat in whatsapp.",
    "tap the contact / group name at the top.",
    "scroll down → “export chat”.",
    "choose “attach media”.",
    "save the .zip to files, or airdrop it here.",
  ],
  android: [
    "open the chat in whatsapp.",
    "tap ⋮ (top-right) → more → “export chat”.",
    "choose “include media”.",
    "send the .zip to yourself, download it here.",
  ],
};

// The machine-readable JSON export exists ONLY in the desktop.telegram.org build.
// Mobile can't export at all; the app-store apps and “Telegram Lite” do HTML at most —
// which this tool can't read. So we send people specifically to the desktop build.
const TG_STEPS: string[] = [
  "use telegram desktop from desktop.telegram.org — only this build does the JSON export.",
  "open the chat → ⋮ → “export chat history”.",
  "set format to “machine-readable JSON” — NOT html.",
  "tick photos, voice & video messages, and stickers.",
  "zip the exported folder, drop the .zip below.",
];

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
      const { job_id } = await uploadChat(file, platform);
      nav(`/job/${job_id}`);
    } catch (e) {
      setErr(friendlyError(e));
      setBusy(false);
    }
  }

  if (!platform) {
    return (
      <Frame
        step="step 1/4 · platform"
        hero="select the platform"
        nav={<button onClick={() => nav("/")}>[ ← back ]</button>}
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

  const steps = platform === "telegram" ? TG_STEPS : WA_STEPS[os];

  return (
    <Frame
      step="step 2/4 · export"
      hero={`export from ${platform}`}
      nav={<button onClick={() => setPlatform(null)}>[ ← back ]</button>}
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
            <div>…then drop the .zip below.</div>
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
              [ {file ? file.name.slice(0, 28) : "choose .zip"} ]
            </button>
            <button
              type="submit"
              className={"opt solid" + (file && (!hosted || agreed) ? " ok" : "")}
              disabled={!file || busy || (hosted && !agreed)}
            >
              [ {busy ? "uploading…" : "upload .zip →"} ]
            </button>
          </div>
          {hosted && (
            <label className="consent">
              <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />
              <span>this is my own conversation and it contains no illegal content.</span>
            </label>
          )}
        </div>
        {err && <p className="err">{err}</p>}
      </form>
    </Frame>
  );
}
