// Shapes returned by the FastAPI backend.

export type State =
  | "uploaded"
  | "inspecting"
  | "ready"
  | "analyzing"
  | "done"
  | "needs_config"
  | "error";

export interface Participant {
  name: string;
  count: number;
}

export interface Progress {
  done: number;
  total: number;
  pct: number;
}

export interface RecentItem {
  file: string;
  type: string;
  caption?: string | null;
}

export interface Stats {
  messages: number;
  date_range: [string, string] | [];
  senders: Record<string, number>;
  media_attached: number;
  media_decoded: number;
}

export interface Deletion {
  raw_media_deleted_at?: string;
  transcript_deleted_at?: string;
}

export interface Retained {
  raw_media: boolean;
  transcript: boolean;
  read: boolean;
}

export interface JobStatus {
  state: State;
  message?: string;
  source?: string;
  me?: string;
  participants?: Participant[];
  progress?: Progress;
  recent?: RecentItem[];
  partial_read?: string;       // the analysis, streaming in token-by-token (during `analyzing`)
  partial_thinking?: string;   // the model's live "thinking" view — process, not prose (during `analyzing`)
  stats?: Stats;
  frontier_ready?: boolean;
  deletion?: Deletion | null;
  retained?: Retained;
  expires_at?: number | null;  // epoch seconds: when this read self-destructs (hosted tier)
  eta_seconds?: number | null; // live, self-correcting estimate of time left in the current phase
  eta_phase?: string;          // "transcribing" | "reading" — which phase the ETA is for
  ts?: number;
}

// A model the user can hand themselves to (the "switcher" / gallery of minds).
// Reuses the route mechanism: each is sent to /send as `route`. The booleans are
// the honest facts the UI surfaces — copy is narrative-thread work.
export interface ReadRoute {
  id: string;
  kind: "managed_api" | "self_host" | "mock";
  model: string;
  label: string;
  lab?: string;            // who made it — "Z-AI" | "DeepSeek" | "Anthropic" | "OpenAI" | "Google" …
  open_weight?: boolean;   // open-weight model vs proprietary (the switcher's headline distinction)
  third_party: boolean;
  zero_retention: boolean;  // served under no-retention (ZDR) vs the provider may keep the transcript
  expect_cold_start: boolean;
  ready: boolean;
}

export interface AppConfig {
  hosted: boolean;
  frontier_ready: boolean;
  default_route?: string;
  routes?: ReadRoute[];
  read_ttl_seconds?: number;   // how long a read lives after it's ready (hosted tier)
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
  me: string;
  read: string;
  citations: number[];
  route?: string;
  model?: string;
  // two-pass read provenance (backend's agentic deepen step)
  first_read?: string;
  inspected?: string[];
  deep_count?: number;
  expires_at?: number | null;  // epoch seconds: when this read self-destructs (hosted tier)
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
