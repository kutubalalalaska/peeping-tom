import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getConfig } from "../api";

// The front door. Framing + the one rule. The copy here is a placeholder — the
// final words come from the narrative thread (narrative-bank.md).
export default function Landing() {
  const [hosted, setHosted] = useState(false);
  const nav = useNavigate();

  useEffect(() => {
    getConfig()
      .then((c) => setHosted(c.hosted))
      .catch(() => undefined);
  }, []);

  return (
    <main className="wrap">
      <h1>Inward Mirror</h1>
      <p className="lede">
        Upload a chat history and a frontier model reads it back to you — the
        patterns you can't see from the inside, every claim cited to the actual
        messages.
      </p>

      <div className="card rules">
        <b>One rule, and it lives in the code</b>
        <ul>
          <li>
            Your media is decoded <b>on this machine</b>.
          </li>
          <li>
            Only the assembled <b>text</b> ever crosses to the model.
          </li>
          <li>You see exactly what crosses — and decide — before it does.</li>
        </ul>
      </div>

      {hosted && (
        <p className="muted small">
          This is the hosted exhibit: your upload is decoded on our server and
          deleted right after. For full privacy, you can self-host.
        </p>
      )}

      <button onClick={() => nav("/start")}>Begin</button>
    </main>
  );
}
