import { useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { uploadChat } from "../api";
import Frame from "./Frame";

type Platform = "whatsapp" | "telegram";
type OS = "iphone" | "android";

const STEPS: Record<OS, string[]> = {
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

export default function Start() {
  const [platform, setPlatform] = useState<Platform | null>(null);
  const [os, setOs] = useState<OS>("iphone");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const nav = useNavigate();

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    setErr(null);
    setBusy(true);
    try {
      const { job_id } = await uploadChat(file, "whatsapp");
      nav(`/job/${job_id}`);
    } catch (e) {
      setErr(String(e));
      setBusy(false);
    }
  }

  if (!platform) {
    return (
      <Frame step="step 1/4 · platform" hero="select the platform">
        <div className="ln">
          <div className="row">
            <button className="opt" onClick={() => setPlatform("whatsapp")}>
              [ whatsapp ]
            </button>
            <button className="opt" disabled>
              [ telegram · soon ]
            </button>
          </div>
        </div>
      </Frame>
    );
  }

  return (
    <Frame
      step="step 2/4 · export"
      hero="export from whatsapp"
      nav={<button onClick={() => setPlatform(null)}>[ ← back ]</button>}
    >
      <form onSubmit={submit}>
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
        <div className="ln" style={{ animationDelay: "80ms" }}>
          <div className="steps">
            {STEPS[os].map((st, i) => (
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
            <button type="submit" className="opt solid" disabled={!file || busy}>
              [ {busy ? "uploading…" : "upload .zip →"} ]
            </button>
          </div>
        </div>
        {err && <p className="err">{err}</p>}
      </form>
    </Frame>
  );
}
