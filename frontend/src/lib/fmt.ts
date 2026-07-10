// Shared time formatting. Coarse and honest — rounds so an estimate never
// looks falsely precise ("~3m left", not "2m47s left").
export function fmtEta(s: number): string {
  if (s == null || !isFinite(s) || s < 0) return "";
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.round(s / 60);
  return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h ${m % 60}m`;
}
