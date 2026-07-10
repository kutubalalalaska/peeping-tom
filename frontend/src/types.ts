// Shapes returned by the FastAPI backend.

export type State =
  | "uploaded"
  | "uploading"
  | "inspecting"
  | "analyzing"
  | "done"
  | "needs_config"
  | "error";

// Machine-readable pipeline phase (status.phase) — what the job is DOING,
// independent of the coarser `state`. The Inspection screen renders from this.
export type Phase =
  | "parsing"
  | "manifest"
  | "reading"
  | "requesting"
  | "decoding"
  | "folding"
  | "composing"
  | "done";

export type Mode = "fast" | "deep";

export interface Progress {
  done: number;
  total: number;
  pct: number | null;
}

export interface RecentItem {
  file: string;
  type: string;
  caption?: string | null;
  reinspected?: boolean;   // this glimpse is the tiered-ASR re-run of a clip, not a first pass
}

// One media request the read made (fast mode): which messages' media it wants
// decoded and WHY — the reason is written for the person waiting.
export interface MediaRequest {
  ids: number[];
  kind?: string;
  reason?: string;
  status: "pending" | "decoding" | "done" | "skipped";
}

// Deep mode: the background decode producer's counters.
export interface DecodeCounter {
  done: number;
  total: number;
  reinspect?: { done: number; total: number } | null;
  eta_seconds?: number | null;
}

// Deep mode: fold-round progress (evidence folded into the working read).
export interface FoldCounter {
  round: number;
  evidence_seen: number;
  evidence_total?: number | null;
}

export interface Retained {
  raw_media: boolean;
  transcript: boolean;
  read: boolean;
}

export interface JobStatus {
  state: State;
  phase?: Phase;
  mode?: Mode;
  message?: string;
  progress?: Progress;
  recent?: RecentItem[];
  media_requests?: MediaRequest[] | null;
  decode?: DecodeCounter | null;
  decode_done?: boolean;
  fold?: FoldCounter | null;
  partial_read?: string;       // the analysis, streaming in token-by-token (during `analyzing`)
  partial_thinking?: string;   // the model's live "thinking" view — process, not prose
  plan?: { tier: number; chunks?: number; est_tokens?: number; script?: string };
  retained?: Retained;
  expires_at?: number | null;  // epoch seconds: when this read self-destructs (hosted tier)
  eta_seconds?: number | null; // live, self-correcting estimate for the current phase
}

// The single read route, as /api/config exposes it (no secrets).
export interface ReadRoute {
  id: string;
  model: string;
  third_party: boolean;
  zero_retention: boolean;
  ready: boolean;
  auth_ok?: boolean | null;
}

export interface AppConfig {
  hosted: boolean;
  routes?: ReadRoute[];
  read_ttl_seconds?: number;   // how long a read lives after it's ready (hosted tier)
  max_upload_mb?: number;      // upload cap — powers the client-side slicer offer
}

// Reads left for this cookie-session (hosted tier). No PII — keyed on an opaque
// session cookie; incognito / cleared cookies simply get a fresh allowance.
export interface Quota {
  enabled: boolean;
  limit: number | null;
  used: number;
  remaining: number | null;
  window_seconds?: number;
}

export interface ReadResult {
  read: string;
  citations: number[];
  citations_dropped?: number;  // invented ids the server stripped (observability)
  mode?: Mode;
  route?: string;
  model?: string;
  inspected?: string[];        // media files decoded for this read
  deep_count?: number;
  expires_at?: number | null;  // epoch seconds: when this read self-destructs (hosted tier)
  slice_range?: string | null; // the date window a too-big export was cut to (client-side)
}

// A cited message, resolved for a clickable [#id] receipt.
export interface ReceiptMedia {
  file: string;
  type: string;
  caption?: string | null;
}

export interface ReceiptMessage {
  id: number;
  ts: string;
  sender: string;
  text: string;
  media: ReceiptMedia[];
}
