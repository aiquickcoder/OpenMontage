// Thin fetch wrapper: same-origin cookie session, X-Studio header on mutations.

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (method !== "GET") headers["X-Studio"] = "1";
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(`/api${path}`, {
    method,
    headers,
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const data = await res.json();
      msg = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? data);
    } catch {
      /* not json */
    }
    if (res.status === 401) window.dispatchEvent(new Event("studio:unauthorized"));
    throw new ApiError(res.status, msg);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};

// ---- types (mirror studio/app.py) ----------------------------------------
export type BriefState =
  | "queued" | "running" | "awaiting_approval" | "approved" | "rejected" | "failed" | "budget";

export interface Credits { budget?: number; spent?: number; reserved?: number; entries?: number }

export interface BriefRow {
  id: string;
  text: string;
  options: Record<string, string>;
  state: BriefState;
  detail: string | null;
  created_at: string;
  updated_at: string;
  stages_done: number;
  stages_total: number;
  current_stage: string | null;
  credits: Credits;
  has_project: boolean;
}

export interface StageRow { stage: string; status: "done" | "current" | "pending" }
export interface FlowCall { ts: string; tool: string; scene_id: string | null; success: boolean; output_path: string; duration_s: number | null }

export interface BriefDetail extends BriefRow {
  events: { state: string; detail: string | null; at: string }[];
  stages: StageRow[];
  flow_calls: FlowCall[];
  autopilot_log: string;
  run_log: string;
  images: string[];
  clips: string[];
  release: { duration?: number; caption?: string; hashtags?: string[] } | null;
}

export interface Overview {
  counts: Partial<Record<BriefState, number>>;
  running: BriefRow[];
  awaiting_count: number;
  recent: BriefRow[];
  services: Record<string, string>;
  server_time: number;
}

export interface VideoRow {
  id: string;
  state: "awaiting_approval" | "approved" | "rejected";
  text: string;
  created_at: string;
  updated_at: string;
  duration: number | null;
  caption: string;
  hashtags: string[];
  platforms: string[];
  files: string[];
  available: boolean;
}
