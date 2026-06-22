import type { ReadRoute } from "../types";

// The honest facts come from the route's booleans — NOT invented copy. Final
// wording is narrative-thread territory; these are placeholders over real facts.
function facts(r: ReadRoute): string[] {
  const out: string[] = [];
  if (r.third_party) {
    out.push(
      r.zero_retention
        ? "the text goes to a managed model — zero-retention"
        : "the text goes to a third-party model"
    );
  } else {
    out.push("stays on our VPS — nothing leaves our stack");
  }
  if (r.expect_cold_start) out.push("may take a moment to wake the model");
  return out;
}

export default function RoutePicker({
  routes,
  selected,
  onSelect,
}: {
  routes: ReadRoute[];
  selected?: string;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="card routes">
      <b>Who reads it?</b>
      <div className="route-cards">
        {routes.map((r) => (
          <button
            type="button"
            key={r.id}
            className={"route-card" + (selected === r.id ? " on" : "")}
            onClick={() => onSelect(r.id)}
          >
            <div className="rc-label">{r.label}</div>
            <div className="rc-model">{r.model}</div>
            <ul className="rc-facts">
              {facts(r).map((f, i) => (
                <li key={i}>{f}</li>
              ))}
            </ul>
          </button>
        ))}
      </div>
      <p className="muted small">
        The same text crosses either way — only your media stayed local. The
        choice is who does the reading.
      </p>
    </div>
  );
}
