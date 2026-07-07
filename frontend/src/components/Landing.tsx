import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import Frame from "./Frame";
import DataFlowModal from "./DataFlowModal";
import { getQuota } from "../api";
import { SPIN } from "../lib/ascii";
import { useSpinFrame } from "../lib/hooks";
import type { Quota } from "../types";

// Entry. Spare by design — the manifesto is held until launch. Copy here is
// placeholder for the narrative thread to refine.
export default function Landing() {
  const [modal, setModal] = useState(false);
  const [quota, setQuota] = useState<Quota | null>(null);
  const nav = useNavigate();
  const spin = SPIN[useSpinFrame(true) % SPIN.length];

  // Reads-left readout (hosted tier only; off-tier the endpoint returns enabled:false).
  useEffect(() => {
    getQuota().then(setQuota).catch(() => undefined);
  }, []);
  return (
    <>
      <Frame 
        step="" 
        hero="Please upload your chat."
      >
        <div className="ln">
          <div className="hint2">
            This demonstration has a zero-retention policy. We process the media locally and we
            use open source LLM providers to analyze the conversations. Once the demonstration is over
            you can delete your data, otherwise it will be automatically erased no later than in 24 hours.
            Please enjoy the demonstration

          </div>
        </div>
        <div className="ln" style={{ animationDelay: "80ms" }}>
          <div className="row" style={{ marginTop: "10px" }}>
            <button className="opt solid" onClick={() => nav("/start")}>
              [ begin → ]
            </button>
          </div>
          {quota?.enabled && quota.remaining !== null && (
            <div className="quota">
              {quota.remaining > 0
                ? `${quota.remaining} of ${quota.limit} reads left today`
                : `you've used all ${quota.limit} reads for today — they reset within a day`}
            </div>
          )}
        </div>
        <div className="ln" style={{ animationDelay: "160ms" }}>
          <div className="links">
            <a
              className="link"
              href="https://github.com/kutubalalalaska/immovable-object-part-1"
              target="_blank"
              rel="noreferrer"
            >
              Source
            </a>
            <button className="link" onClick={() => setModal(true)}>
              Data cycle&nbsp;{spin}
            </button>
          </div>
        </div>
      </Frame>
      <DataFlowModal open={modal} onClose={() => setModal(false)} />
    </>
  );
}
