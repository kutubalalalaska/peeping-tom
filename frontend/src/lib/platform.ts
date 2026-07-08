// Best-effort current-device OS detection — drives the export guidance (how to
// get the .zip into the upload differs per device, and Telegram export can't be
// done on a phone at all). Heuristic, never load-bearing: the user can still
// override the platform/OS toggle, and upload is never hard-blocked.

export type OSName = "ios" | "android" | "desktop";

export function detectOS(): OSName {
  if (typeof navigator === "undefined") return "desktop";
  const ua = navigator.userAgent || "";
  if (/android/i.test(ua)) return "android";
  // iPadOS 13+ reports a Mac UA — catch it via touch points.
  const iOS =
    /iphone|ipad|ipod/i.test(ua) ||
    (/(macintosh|mac os x)/i.test(ua) && (navigator.maxTouchPoints || 0) > 1);
  return iOS ? "ios" : "desktop";
}

export const isMobileOS = (os: OSName) => os === "ios" || os === "android";
