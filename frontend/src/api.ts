// Typed client for the backend API. One function per endpoint.

import type {
  AppConfig,
  JobStatus,
  Quota,
  ReadResult,
  ReceiptMessage,
  Retained,
} from "./types";

async function asJson<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return (await r.json()) as T;
}

export const getConfig = async () => asJson<AppConfig>(await fetch("/api/config"));

// Reads left for this cookie-session (Landing readout, hosted tier).
export const getQuota = async () => asJson<Quota>(await fetch("/api/quota"));

// `lang` (the chosen UI language) rides along so the read comes back in it.
export async function uploadChat(file: File, source: string, lang?: string) {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("source", source);
  if (lang) fd.append("lang", lang);
  return asJson<{ job_id: string }>(
    await fetch("/api/upload", { method: "POST", body: fd })
  );
}

export const getStatus = async (id: string) =>
  asJson<JobStatus>(await fetch(`/api/jobs/${id}`));

export async function setRole(id: string, me: string) {
  const fd = new FormData();
  fd.append("me", me);
  return asJson<{ ok: boolean; me: string }>(
    await fetch(`/api/jobs/${id}/role`, { method: "POST", body: fd })
  );
}

// Cross the boundary. `route` (a read-route id) is optional — omitted, the
// backend uses the default route. See READ_ROUTES.md.
export async function sendRead(id: string, route?: string) {
  const fd = new FormData();
  if (route) fd.append("route", route);
  return asJson<{ ok: boolean; route?: string }>(
    await fetch(`/api/jobs/${id}/send`, { method: "POST", body: fd })
  );
}

export const getResult = async (id: string) =>
  asJson<ReadResult>(await fetch(`/api/jobs/${id}/result`));

// Resolve cited message ids -> messages, for clickable [#id] receipts.
export const getMessages = async (id: string, ids: number[]) =>
  asJson<ReceiptMessage[]>(
    await fetch(`/api/jobs/${id}/messages?ids=${ids.join(",")}`)
  );

// The WHOLE chat (no ids filter) — powers the context drawer. May be large; the
// caller windows the render. 404s once the chat is purged (ephemeral / after TTL).
export const getAllMessages = async (id: string) =>
  asJson<ReceiptMessage[]>(await fetch(`/api/jobs/${id}/messages`));

export const getRetained = async (id: string) =>
  asJson<Retained>(await fetch(`/api/jobs/${id}/retained`));

export const deleteJob = async (id: string) =>
  asJson<{ deleted: boolean; at: string }>(
    await fetch(`/api/jobs/${id}`, { method: "DELETE" })
  );

export const transcriptUrl = (id: string) => `/api/jobs/${id}/transcript`;
export const mediaUrl = (id: string, name: string) =>
  `/api/jobs/${id}/media/${encodeURIComponent(name)}`;
