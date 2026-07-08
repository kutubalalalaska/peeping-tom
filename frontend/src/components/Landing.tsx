import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import Frame from "./Frame";
import DataFlowModal from "./DataFlowModal";
import { getQuota } from "../api";
import { SPIN } from "../lib/ascii";
import { useSpinFrame } from "../lib/hooks";
import { useT } from "../lib/i18n";
import type { Quota } from "../types";

// Entry. Spare by design — the manifesto is held until launch. Copy here is
// placeholder for the narrative thread to refine.
export default function Landing() {
  const [modal, setModal] = useState(false);
  const [quota, setQuota] = useState<Quota | null>(null);
  const nav = useNavigate();
  const { t } = useT();
  const spin = SPIN[useSpinFrame(true) % SPIN.length];

  // Reads-left readout (hosted tier only; off-tier the endpoint returns enabled:false).
  useEffect(() => {
    getQuota().then(setQuota).catch(() => undefined);
  }, []);
  return (
    <>
      <Frame
        step=""
        hero={t("landing.hero")}
      >
        <div className="ln">
          <div className="hint2">{t("landing.blurb")}</div>
        </div>
        <div className="ln" style={{ animationDelay: "80ms" }}>
          <div className="row" style={{ marginTop: "10px" }}>
            <button className="opt solid" onClick={() => nav("/start")}>
              {t("landing.begin")}
            </button>
          </div>
          {quota?.enabled && quota.remaining !== null && (
            <div className="quota">
              {quota.remaining > 0
                ? t("landing.quotaLeft", { remaining: quota.remaining, limit: quota.limit ?? 0 })
                : t("landing.quotaNone", { limit: quota.limit ?? 0 })}
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
              {t("landing.source")}
            </a>
            <button className="link" onClick={() => setModal(true)}>
              {t("landing.dataCycle")}&nbsp;{spin}
            </button>
          </div>
        </div>
      </Frame>
      <DataFlowModal open={modal} onClose={() => setModal(false)} />
    </>
  );
}
