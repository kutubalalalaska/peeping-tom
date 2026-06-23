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
  partial_read?: string;   // the read, streaming in token-by-token (during `analyzing`)
  stats?: Stats;
  frontier_ready?: boolean;
  deletion?: Deletion | null;
  retained?: Retained;
  ts?: number;
}

// A read backend the user can be routed to (see READ_ROUTES.md). The four
// booleans are the honest facts the UI surfaces — copy is narrative-thread work.
export interface ReadRoute {
  id: string;
  kind: "managed_api" | "self_host" | "mock";
  model: string;
  label: string;
  third_party: boolean;
  zero_retention: boolean;
  expect_cold_start: boolean;
  ready: boolean;
}

export interface AppConfig {
  hosted: boolean;
  frontier_ready: boolean;
  default_route?: string;
  routes?: ReadRoute[];
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
