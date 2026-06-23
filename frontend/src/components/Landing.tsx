import { useState } from "react";
import { useNavigate } from "react-router-dom";
import Frame from "./Frame";
import DataFlowModal from "./DataFlowModal";

// Entry. Spare by design — the manifesto is held until launch. Copy here is
// placeholder for the narrative thread to refine.
export default function Landing() {
  const [modal, setModal] = useState(false);
  const [panel, setPanel] = useState(false);
  const nav = useNavigate();
  return (
    <>
      <Frame step="immovable object" hero="a mirror, pointed inward">
        <div className="ln">
          <div className="hint2">
            upload a chat. it's decoded on this machine — only the text is read by a
            frontier model, which reads your patterns back to you, cited to the
            actual messages.
          </div>
        </div>
        <div className="ln" style={{ animationDelay: "80ms" }}>
          <div className="row" style={{ marginTop: "10px" }}>
            <button className="opt solid" onClick={() => nav("/start")}>
              [ begin → ]
            </button>
          </div>
        </div>
        <div className="ln" style={{ animationDelay: "160ms" }}>
          <div className="links">
            <button className="link" onClick={() => setPanel(!panel)}>
              how to run it yourself
            </button>
            <button className="link" onClick={() => setModal(true)}>
              how your data is processed →
            </button>
          </div>
          {panel && (
            <p className="panel">
              open-source · <span className="pre">docker compose up</span> on your
              own machine. the raw media never leaves your control.
            </p>
          )}
        </div>
      </Frame>
      <DataFlowModal open={modal} onClose={() => setModal(false)} />
    </>
  );
}
