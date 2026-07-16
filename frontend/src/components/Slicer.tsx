import { useEffect, useMemo, useState } from "react";
import { useT } from "../lib/i18n";
import { progBar, spanBar } from "../lib/ascii";
import {
  ExportFormatError,
  ONE_PASS_TOKENS,
  buildSlice,
  fitsBudget,
  openExport,
  planFrom,
  rangeBytes,
  rangeLabel,
  rangeTokens,
  type ExportModel,
  type SliceBudget,
  type SliceMeta,
} from "../lib/slicer";

const GB = 1024 * 1024 * 1024;
const fmtSize = (b: number) =>
  b >= GB ? `${(b / GB).toFixed(2)} GB` : `${Math.max(1, Math.round(b / 1048576))} MB`;

// Shown when the chosen zip exceeds the upload cap: parse it LOCALLY, let the
// user keep a date window (latest / earliest / middle, fine-tuned by sliders),
// and hand back a rebuilt smaller zip + an honest range label. Nothing uploads
// until the user continues with the slice.
export default function Slicer({
  file,
  source,
  capMB,
  onReady,
  onCancel,
}: {
  file: File;
  source: "whatsapp" | "telegram";
  capMB: number;
  onReady: (sliced: File, meta: SliceMeta) => void;
  onCancel: () => void;
}) {
  const { t } = useT();
  const [model, setModel] = useState<ExportModel | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [range, setRange] = useState<[number, number]>([0, 0]);
  const [bound, setBound] = useState<"tokens" | "bytes" | "all">("all");
  const [building, setBuilding] = useState<[number, number] | null>(null);

  // Two budgets a slice must fit: the upload cap's bytes (minus zip headroom)
  // AND the one-pass read window in estimated tokens — so anything that leaves
  // this screen is guaranteed a single coherent read.
  const budget: SliceBudget = useMemo(
    () => ({ bytes: Math.floor(capMB * 1024 * 1024 * 0.97), tokens: ONE_PASS_TOKENS }),
    [capMB]
  );

  // The ONE slider: its value is the window's start; the window itself always
  // fills the budget from there (planFrom), so dragging slides a full
  // one-read window along the chat.
  function apply(m: ExportModel, start: number) {
    const p = planFrom(m, budget, start);
    setRange(p.range);
    setBound(p.bound);
  }

  useEffect(() => {
    let alive = true;
    openExport(file, source)
      .then((m) => {
        if (!alive) return;
        if (!m.msgs.length) throw new Error("no dated messages found");
        setModel(m);
        apply(m, m.msgs.length);                   // most people want the latest part
      })
      .catch((ex) => {
        if (!alive) return;
        // Wrong-format zips get the pointed message; anything else the generic one.
        setErr(ex instanceof ExportFormatError
          ? (ex.found && ex.found !== source
              ? t("start.errWrongPlatform", { found: ex.found, selected: source })
              : t("start.errNotExport", { platform: source }))
          : t("slice.failed"));
      });
    return () => {
      alive = false;
    };
  }, [file, source]); // eslint-disable-line react-hooks/exhaustive-deps

  const [from, to] = range;
  const est = useMemo(
    () => (model && to > from ? rangeBytes(model, from, to) : 0),
    [model, from, to]
  );
  const estTok = useMemo(
    () => (model && to > from ? rangeTokens(model, from, to) : 0),
    [model, from, to]
  );
  const fits = !!model && to > from && fitsBudget(model, from, to, budget);
  // Which axis triggered the slicer: raw size, or sheer message volume.
  const tooLong = file.size <= capMB * 1024 * 1024;
  const n = model?.msgs.length ?? 0;
  const step = Math.max(1, Math.floor(n / 400));

  async function cut() {
    if (!model || !fits) return;
    setBuilding([0, 1]);
    try {
      const { file: sliced, meta } = await buildSlice(model, from, to, (d, tot) =>
        setBuilding([d, tot])
      );
      onReady(sliced, meta);
    } catch {
      setErr(t("slice.failed"));
      setBuilding(null);
    }
  }

  if (err) {
    return (
      <div className="slicebox">
        <p className="err">{err}</p>
        <button type="button" className="opt" onClick={onCancel}>
          {t("slice.cancel")}
        </button>
      </div>
    );
  }

  if (!model) {
    return (
      <div className="slicebox">
        <div className="notice">
          <strong>{tooLong
            ? t("slice.tooLong")
            : t("slice.tooBig", { size: fmtSize(file.size), cap: fmtSize(capMB * 1048576) })}</strong>
          {t("slice.reading")}
        </div>
      </div>
    );
  }

  if (building) {
    const [d, tot] = building;
    const pct = tot ? Math.round((d / tot) * 100) : 0;
    return (
      <div className="slicebox">
        <div className="hint2">{t("slice.building", { done: d, total: tot })}</div>
        <pre className="uppre">{progBar(pct)}</pre>
      </div>
    );
  }

  return (
    <div className="slicebox">
      <div className="notice">
        <strong>{tooLong
            ? t("slice.tooLong")
            : t("slice.tooBig", { size: fmtSize(file.size), cap: fmtSize(capMB * 1048576) })}</strong>
        {t("slice.pick")}
      </div>
      <div className="row">
        <button type="button" className="opt" onClick={() => apply(model, n)}>
          {t("slice.latest")}
        </button>
        <button type="button" className="opt" onClick={() => apply(model, 0)}>
          {t("slice.earliest")}
        </button>
        <button type="button" className="opt" onClick={() => apply(model, Math.floor((n - (to - from)) / 2))}>
          {t("slice.middle")}
        </button>
      </div>
      <div className="slicers">
        <pre className="spanbar">{spanBar(from / n, to / n)}</pre>
        <label>
          <span>{t("slice.window")}</span>
          <input
            type="range"
            min={0}
            max={n}
            step={step}
            value={from}
            onChange={(e) => apply(model, +e.target.value)}
          />
        </label>
      </div>
      <div className="slicestat">
        {t("slice.selected", { n: to - from, range: rangeLabel(model, from, to) })}
        {" · "}
        <span className={bound === "tokens" ? "lim" : undefined}>
          ≈ {Math.round(estTok / 1000)}k / {Math.round(budget.tokens / 1000)}k {t("slice.unitTokens")}
        </span>
        {" · "}
        <span className={bound === "bytes" ? "lim" : undefined}>
          {fmtSize(est)} / {fmtSize(budget.bytes)}
        </span>{" "}
        <span className={fits ? "fit-ok" : "fit-no"}>
          {fits ? t("slice.fits") : t("slice.over")}
        </span>
      </div>
      <div className="row">
        <button type="button" className={"opt solid" + (fits ? " ok" : "")} disabled={!fits} onClick={cut}>
          {t("slice.cut")}
        </button>
        <button type="button" className="opt" onClick={onCancel}>
          {t("slice.cancel")}
        </button>
      </div>
    </div>
  );
}
