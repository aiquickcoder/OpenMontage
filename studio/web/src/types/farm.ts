// Phone farm types (mirror studio/farm.py).

export type Platform = "instagram" | "tiktok" | "youtube" | "vk";
export type JobStatus = "pending" | "running" | "done" | "failed" | "cancelled";
export type JobKind = "publish" | "warmup" | "audience";
export type CommandStatus = "queued" | "sent" | "done" | "failed";

export interface FarmStatus {
  online: boolean;               // heartbeat younger than 90 s
  connected: boolean;            // socket open right now
  last_seen: number | null;
  last_heartbeat: number | null;
  agent_version: string | null;
  host: string | null;
  scheduler_alive: boolean | null;
  scheduler_heartbeat_at: number | null;
  snapshot_at: number | null;
  queued_commands: number;
  server_time: number;
}

export interface DeviceStatus {
  name: string;
  serial: string;
  adb_state: string;             // device | offline | unauthorized | missing …
  ok: boolean;
  problems: string[];
  info: Record<string, unknown>; // battery, storage, vpn, app versions — whatever the agent reports
  checked_at: number | null;
  paused: boolean;
  busy: boolean;
}

export interface Account {
  id: string;
  platform: Platform;
  username: string;
  device: string;
  started_at?: string;
  mode?: "warmup" | "audience";
  sessions_per_day?: number | string;  // 3 or "2-3"
  working_hours?: string[];
  allow_follows?: boolean;
  hashtags?: string[];
  bloggers?: string[];
  package?: string;
  [extra: string]: unknown;
}

export type AccountInput = Partial<Omit<Account, "working_hours" | "hashtags" | "bloggers">> & {
  working_hours?: string[] | string;
  hashtags?: string[] | string;
  bloggers?: string[] | string;
};

export interface FarmJob {
  id: number;
  kind: JobKind;
  account_id: string;
  device: string;
  release_id: string | null;
  status: JobStatus;
  payload: Record<string, unknown>;
  result: Record<string, unknown> | null;
  run_after: number | null;
  created_at: number | null;
  started_at: number | null;
  finished_at: number | null;
  updated_at: number;
}

export interface Device {
  name: string;
  serial: string;
  require_vpn: boolean;
  pinned_apps: Record<string, string>;
  status: DeviceStatus | null;
  accounts: Account[];
  current_job: FarmJob | null;
  next_job: FarmJob | null;
  pending_jobs: number;
  live_viewers: number;
}

export interface AccountsResponse {
  accounts: Account[];
  devices: string[];
  platforms: Platform[];
  pending: boolean;              // an edit is waiting for the farm to apply it
}

export interface CommandResult<R = Record<string, unknown>> {
  command_id: string;
  kind: string;
  status: CommandStatus;
  result: R | null;
  error: string | null;
}

export interface FarmCommand {
  id: string;
  kind: string;
  args: Record<string, unknown>;
  status: CommandStatus;
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: number;
  sent_at: number | null;
  finished_at: number | null;
}

export interface PublicationCell {
  job_id: number;
  account_id: string;
  platform: Platform | null;
  device: string;
  status: JobStatus;
  confirmed: boolean;
  url: string | null;
  error: string | null;
  run_after: number | null;
  created_at: number | null;
  started_at: number | null;
  finished_at: number | null;
  updated_at: number;
  attempts: number;
  artifacts: string[];           // absolute /api/farm/artifacts/... urls
}

export interface PublicationRow {
  release_id: string | null;
  cells: Record<string, PublicationCell>;
  updated_at: number;
}

export interface PublicationsResponse {
  rows: PublicationRow[];
  accounts: { id: string; platform: Platform | null }[];      // columns in use
  all_accounts: { id: string; platform: Platform | null }[];  // for the filter
}

export interface PublishInput {
  accounts: string[];
  start_at: string | null;       // ISO 8601
  spread_minutes: number;
}
