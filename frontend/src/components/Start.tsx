import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { uploadChat } from "../api";

type Platform = "iphone" | "android";

// Real export steps — accuracy at step one is exactly where this project refuses
// to fake it. The export happens on the phone, then the .zip comes to this machine.
const STEPS: Record<Platform, string[]> = {
  iphone: [
    "Open the chat in WhatsApp.",
    "Tap the contact or group name at the top.",
    "Scroll down and tap “Export Chat”.",
    "Choose “Attach Media”.",
    "Save the .zip to Files, or AirDrop it to this computer.",
  ],
  android: [
    "Open the chat in WhatsApp.",
    "Tap ⋮ (top-right) → More → “Export chat”.",
    "Choose “Include media”.",
    "Send the .zip to yourself (email / Drive) and download it here.",
  ],
};

export default function Start() {
  const [platform, setPlatform] = useState<Platform>("iphone");
  const [file, setFile] = useState<File | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
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

  return (
    <main className="wrap">
      <h1>Bring your chat</h1>

      <section className="sources">
        <div className="source on">WhatsApp</div>
        <div className="source off">
          Telegram <span>soon</span>
        </div>
      </section>

      <div className="card">
        <div className="seg">
          <button
            type="button"
            className={platform === "iphone" ? "on" : ""}
            onClick={() => setPlatform("iphone")}
          >
            iPhone
          </button>
          <button
            type="button"
            className={platform === "android" ? "on" : ""}
            onClick={() => setPlatform("android")}
          >
            Android
          </button>
        </div>
        <ol className="steps">
          {STEPS[platform].map((s, i) => (
            <li key={i}>{s}</li>
          ))}
        </ol>
        <p className="muted small">…then send the .zip over here.</p>
      </div>

      <form onSubmit={submit} className="card">
        <input
          type="file"
          accept=".zip"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          required
        />
        <button type="submit" disabled={busy || !file}>
          {busy ? "Uploading…" : "Upload & decode locally"}
        </button>
      </form>
      {err && <p className="err">{err}</p>}
    </main>
  );
}
