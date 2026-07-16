import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import Frame from "./Frame";
import DataFlowModal from "./DataFlowModal";
import { getConfig, getQuota } from "../api";
import { SPIN } from "../lib/ascii";
import { useSpinFrame } from "../lib/hooks";
import { useT } from "../lib/i18n";
import type { AppConfig, Quota } from "../types";

const GITHUB_URL = "https://github.com/kutubalalalaska/immovable-object-part-1";
const SUPPORT_URL = "https://instagram.com/syndinc";

// Entry. Spare by design — the manifesto is held until launch. Copy here is
// placeholder for the narrative thread to refine.
export default function Landing() {
  const [modal, setModal] = useState(false);
  const [quota, setQuota] = useState<Quota | null>(null);
  const [cfg, setCfg] = useState<AppConfig | null>(null);
  const nav = useNavigate();
  const { t } = useT();
  const spin = SPIN[useSpinFrame(true) % SPIN.length];

  // Reads-left readout (hosted tier only; off-tier the endpoint returns enabled:false)
  // + the out-of-credits notice (honesty: don't let uploads march into a dead read).
  useEffect(() => {
    getQuota().then(setQuota).catch(() => undefined);
    getConfig().then(setCfg).catch(() => undefined);
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
            <button
              className="opt solid"
              disabled={!!cfg?.out_of_credits}
              onClick={() => nav("/start")}
            >
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
          {cfg?.out_of_credits && (
            <div className="notice" style={{ marginTop: "10px" }}>
              {t("landing.noCredits")}{" "}
              <a className="link" href={GITHUB_URL} target="_blank" rel="noreferrer">
                {t("landing.noCreditsRun")}
              </a>
              {t("landing.noCreditsMid")}
              <a className="link" href={SUPPORT_URL} target="_blank" rel="noreferrer">
                {t("landing.noCreditsSub")}
              </a>
              {t("landing.noCreditsTail")}
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
