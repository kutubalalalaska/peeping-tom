import { useEffect, useRef, useState } from "react";

// Type a string in, character by character (26 ms/char per the design spec).
export function useTypeIn(text: string, speed = 26): string {
  const [shown, setShown] = useState("");
  useEffect(() => {
    let i = 0;
    let timer: number;
    setShown("");
    const tick = () => {
      i++;
      setShown(text.slice(0, i));
      if (i < text.length) timer = window.setTimeout(tick, speed);
    };
    timer = window.setTimeout(tick, speed);
    return () => window.clearTimeout(timer);
  }, [text, speed]);
  return shown;
}

// A frame counter (~14 fps) that drives the braille spinner / audio transport.
export function useSpinFrame(active = true): number {
  const [sf, setSf] = useState(0);
  const ref = useRef(0);
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => {
      ref.current += 1;
      setSf(ref.current);
    }, 70);
    return () => window.clearInterval(t);
  }, [active]);
  return sf;
}
