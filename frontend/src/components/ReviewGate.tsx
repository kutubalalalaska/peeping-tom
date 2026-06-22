import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { getConfig, getStatus, sendRead, transcriptUrl } from "../api";
import type { AppConfig, JobStatus, ReadRoute } from "../types";
import RoutePicker from "./RoutePicker";

// The review gate — the one deliberate moment anything leaves the machine.
// See exactly what crosses, choose who reads it (only on the hosted exhibit,
// where ≥2 routes are ready), then send.
export default function ReviewGate() {
  const { id } = useParams<{ id: string }>();
  const [cfg, setCfg] = useState<AppConfig | null>(null);
  const [transcript, setTranscript] = useState<string>("");
  const [route, setRoute] = useState<string | undefined>(undefined);
  const [sending, setSending] = useState(false);
  const [status, setStatus] = useState<JobStatus | null>(null);
  const nav = useNavigate();

  useEffect(() => {
    if (!id) return;
    getConfig()
      .then((c) => {
        setCfg(c);
        setRoute(c.default_route ?? c.routes?.find((r) => r.ready)?.id);
      })
      .catch(() => undefined);
    fetch(transcriptUrl(id))
      .then((r) => r.text())
      .then((t) => setTranscript(t.replace(/^<pre>/, "").replace(/<\/pre>\s*$/, "")))
      .catch(() => undefined);
  }, [id]);

  // Once sent, poll the read to completion and hand off to the result page.
  useEffect(() => {
    if (!id || !sending) return;
    const t = setInterval(async () => {
      let st: JobStatus;
      try {
        st = await getStatus(id);
      } catch {
        return;
      }
      setStatus(st);
      if (st.state === "done") {
        clearInterval(t);
        nav(`/result/${id}`);
      }
      if (st.state === "error" || st.state === "needs_config") clearInterval(t);
    }, 1000);
    return () => clearInterval(t);
  }, [id, sending, nav]);

  const routes = cfg?.routes ?? [];
  const readyRoutes = routes.filter((r) => r.ready);
  const showPicker = readyRoutes.length >= 2;
  const chosen: ReadRoute | undefined = routes.find((r) => r.id === route);

  async function send() {
    if (!id) return;
    setSending(true);
    await sendRead(id, showPicker ? route : undefined).catch(() => undefined);
  }

  if (sending) {
    const st = status?.state;
    if (st === "needs_config")
      return (
        <main className="wrap">
          <h1>Almost there</h1>
          <p className="err">{status?.message}</p>
        </main>
      );
    if (st === "error")
      return (
        <main className="wrap">
          <h1>Something went wrong</h1>
          <p className="err">{status?.message}</p>
        </main>
      );
    return (
      <main className="wrap">
        <h1>Reading…</h1>
        <div className="spinner big-spin" />
        <p className="muted">
          {chosen?.expect_cold_start
            ? "Waking your private model on our VPS — nothing leaves to any API."
            : `The model is reading your chat${chosen?.model ? ` (${chosen.model})` : ""}. Only the text transcript crossed — nothing else left this machine.`}
        </p>
      </main>
    );
  }

  return (
    <main className="wrap">
      <h1>Before it crosses</h1>
      <p className="muted">
        This is the one moment anything leaves your machine. Read exactly what
        will be sent — only this text, never your media.
      </p>

      <div className="card">
        <b>The exact text that will be sent</b>
        <pre className="xscript">{transcript || "Loading the transcript…"}</pre>
      </div>

      {showPicker && (
        <RoutePicker routes={readyRoutes} selected={route} onSelect={setRoute} />
      )}

      <button onClick={send} disabled={sending || (showPicker && !route)}>
        Send across the boundary
        {showPicker && chosen ? ` — ${chosen.label}` : ""}
      </button>
    </main>
  );
}
