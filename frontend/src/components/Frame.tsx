import type { ReactNode } from "react";
import { useTypeIn } from "../lib/hooks";

// The persistent terminal shell: a stepline, the `> typed hero + caret`, a
// scrolling content area, and a footer (nav + custody line). Each screen renders
// its own Frame; the hero re-types on mount, matching the "connected" transition.
export default function Frame({
  step,
  hero,
  children,
  custody,
  nav,
  top,
  run,
}: {
  step: string;
  hero: string;
  children: ReactNode;
  custody?: string;
  nav?: ReactNode;
  top?: boolean;
  run?: string;
}) {
  const typed = useTypeIn(hero);
  return (
    <>
      <div className="meta">
        <span>immovable object</span>
        <span>{run ?? ""}</span>
      </div>
      <div className="frame">
        <div className="stepline">
          <span>{step}</span>
          <span className="dim">immovable object</span>
        </div>
        <div className="hero">
          &gt; {typed}
          <span className="cur" />
        </div>
        <div className={"content" + (top ? " top" : "")}>{children}</div>
        <div className="foot">
          <div className="nav">{nav}</div>
          <span className="custody">
            {custody ?? "processed on this machine — nothing leaves it"}
          </span>
        </div>
      </div>
    </>
  );
}
