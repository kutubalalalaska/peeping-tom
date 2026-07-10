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

// ---- resumable chunked upload (mirror/uploads.py) ----
// init -> part(offset) … -> complete. Survives a dropped connection (resume from
// the server's byte offset) and never holds the whole file in memory.

function fdOf(fields: Record<string, string | number>): FormData {
  const fd = new FormData();
  for (const [k, v] of Object.entries(fields)) fd.append(k, String(v));
  return fd;
}

// Bytes the server already holds for this upload — the resume point.
export const getUploadOffset = async (jid: string) =>
  (await asJson<{ received: number; size: number }>(await fetch(`/api/upload/${jid}/offset`))).received;

export interface SliceMetaFields {
  range: string;   // the kept window's dates
  before: number;  // messages before the window (not uploaded)
  after: number;   // messages after it
  full: string;    // the original corpus's full span
}

export interface ChunkedOpts {
  onProgress?: (received: number, total: number) => void;
  signal?: AbortSignal;
  sliceMeta?: SliceMetaFields;   // provenance from the local slicer
}

export async function uploadChatChunked(
  file: File,
  source: string,
  lang: string,
  mode: string,
  opts: ChunkedOpts = {}
): Promise<{ job_id: string }> {
  const { onProgress, signal, sliceMeta } = opts;
  const init = await asJson<{ job_id: string; chunk_size: number; max_mb: number }>(
    await fetch("/api/upload/init", {
      method: "POST",
      body: fdOf({ source, lang, mode, size: file.size, name: file.name,
                   ...(sliceMeta ? { slice_range: sliceMeta.range, slice_before: sliceMeta.before,
                                     slice_after: sliceMeta.after, slice_full: sliceMeta.full } : {}) }),
      signal,
    })
  );
  const jid = init.job_id;
  const chunkSize = init.chunk_size || 8 * 1024 * 1024;
  let received = 0;
  let fails = 0;
  onProgress?.(0, file.size);

  while (received < file.size) {
    if (signal?.aborted) throw new DOMException("aborted", "AbortError");
    const blob = file.slice(received, Math.min(received + chunkSize, file.size));
    try {
      const r = await fetch(`/api/upload/${jid}/part?offset=${received}`, {
        method: "POST",
        body: blob,
        signal,
      });
      if (r.status === 409) {
        // Offset mismatch: the server tells us where it actually is; re-sync + retry.
        const b = await r.json().catch(() => null);
        received = typeof b?.detail?.received === "number" ? b.detail.received : await getUploadOffset(jid);
        continue;
      }
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const j = (await r.json()) as { received: number };
      received = typeof j.received === "number" ? j.received : received + blob.size;
      fails = 0;
      onProgress?.(received, file.size);
    } catch (e) {
      if (signal?.aborted) throw e;
      if (++fails > 5) throw e; // give up after repeated failures on the same chunk
      await new Promise((res) => setTimeout(res, 800 * fails));
      try {
        received = await getUploadOffset(jid); // re-sync before retrying
      } catch {
        /* keep our offset and retry */
      }
    }
  }

  return asJson<{ job_id: string }>(
    await fetch(`/api/upload/${jid}/complete`, { method: "POST", signal })
  );
}

export const getStatus = async (id: string) =>
  asJson<JobStatus>(await fetch(`/api/jobs/${id}`));

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
