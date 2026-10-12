// Series worlds + episode planner types/helpers (mirror studio/worlds.py, studio/planner.py).
import { ApiError } from "../api";
import type { CharacterVoice, TaskBrief, TaskStatus } from "./characters";

export type RefKind = "style" | "location" | "family";
export type UploadKind = "cover" | RefKind;

export interface WorldRef { kind: RefKind; path: string; name: string }

export interface DraftCharacter {
  name: string;
  role: string;
  description: string;
  prompt_tokens: string;
  voice: CharacterVoice;
}

export interface WorldDraft {
  name: string;
  logline: string;
  audience: string;
  setting: string;
  style_lock: string;
  negative_style: string;
  episode_rules: string;
  characters: DraftCharacter[];
}

export interface WorldTask { id: string; kind: string; status: TaskStatus; error: string | null; updated_at: string }

export interface WorldCharacter {
  id: string;
  name: string;
  role: string;
  turnaround_path: string;
  task: TaskBrief | null;
}

export interface WorldFields {
  name: string;
  label: string;
  logline: string;
  audience: string;
  setting: string;
  style_lock: string;
  negative_style: string;
  episode_rules: string;
  duration: string;
}

export interface World extends WorldFields {
  id: string;
  cover: string;
  refs: WorldRef[];
  source_prompt: string;
  draft: WorldDraft | null;
  task_id: string | null;
  task: WorldTask | null;
  archived: boolean;
  created_at: string;
  updated_at: string;
  character_count: number;
  characters?: WorldCharacter[]; // detail only
}

export interface ApplyDraftCharacter extends DraftCharacter { generate: boolean }
export interface ApplyDraftInput extends Partial<WorldFields> { characters: ApplyDraftCharacter[]; variants: number }

/** Generic background task (GET /api/tasks/{id}). */
export interface StudioTask {
  id: string;
  kind: string;
  args: Record<string, unknown>;
  status: TaskStatus;
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export type EpisodeStatus = "draft" | "approved" | "queued" | "done" | "failed";

export interface Episode {
  id: string;
  world_id: string;
  air_date: string; // YYYY-MM-DD
  title: string;
  logline: string;
  synopsis: string;
  characters: string[];
  notes: string;
  status: EpisodeStatus;
  source: "ai" | "manual";
  brief_id: string | null;
  released_at: string | null;
  created_at: string;
  updated_at: string;
}

export const EDITABLE_EPISODE: EpisodeStatus[] = ["draft", "approved"];

export const EPISODE_STATUS_LABEL: Record<EpisodeStatus, string> = {
  draft: "Черновик",
  approved: "Утверждён",
  queued: "В производстве",
  done: "Выпущен",
  failed: "Ошибка",
};

export const WORLD_TASK_LABEL: Record<TaskStatus, string> = {
  queued: "В очереди",
  running: "Claude думает",
  done: "Готово",
  failed: "Ошибка",
};

export const UPLOAD_KIND_LABEL: Record<UploadKind, string> = {
  cover: "Обложка",
  style: "Стиль",
  location: "Локации",
  family: "Персонажи / семья",
};

export const WORLD_FIELD_LABEL: Record<keyof WorldFields, string> = {
  name: "Название",
  label: "Метка (короткая подпись)",
  logline: "Логлайн",
  audience: "Аудитория",
  setting: "Мир и сеттинг",
  style_lock: "Стиль (style lock)",
  negative_style: "Чего избегать в стиле",
  episode_rules: "Правила эпизодов",
  duration: "Длительность эпизода, с",
};

export const WORLD_FIELD_MAX: Record<keyof WorldFields, number> = {
  name: 80,
  label: 120,
  logline: 2000,
  audience: 600,
  setting: 3000,
  style_lock: 1500,
  negative_style: 800,
  episode_rules: 3000,
  duration: 20,
};

export const worldFileUrl = (id: string, rel: string, w?: number) =>
  `/api/worlds/${encodeURIComponent(id)}/file/${rel.split("/").map(encodeURIComponent).join("/")}${w ? `?w=${w}` : ""}`;

export const MAX_IMAGE = 15 * 1024 * 1024;
export const MAX_ARCHIVE = 200 * 1024 * 1024;

/** Raw-body POST (api.ts only speaks JSON). XHR so big uploads can report progress (0..1). */
export function uploadRaw<T>(path: string, body: Blob, contentType: string, onProgress?: (p: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api${path}`);
    xhr.withCredentials = true;
    xhr.setRequestHeader("X-Studio", "1");
    xhr.setRequestHeader("Content-Type", contentType || "application/octet-stream");
    if (onProgress) xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onerror = () => reject(new ApiError(0, "Сеть недоступна"));
    xhr.onload = () => {
      let data: unknown = null;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        /* not json */
      }
      if (xhr.status >= 200 && xhr.status < 300) return resolve(data as T);
      if (xhr.status === 401) window.dispatchEvent(new Event("studio:unauthorized"));
      const detail = (data as { detail?: unknown } | null)?.detail;
      reject(new ApiError(xhr.status, typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : xhr.statusText));
    };
    xhr.send(body);
  });
}

export function uploadWorldImage(id: string, kind: UploadKind, file: File): Promise<{ path: string; world: World }> {
  if (file.size > MAX_IMAGE) return Promise.reject(new ApiError(413, `${file.name}: больше 15 МБ`));
  const qs = new URLSearchParams({ kind, filename: file.name });
  return uploadRaw(`/worlds/${encodeURIComponent(id)}/image?${qs.toString()}`, file, file.type || "application/octet-stream");
}

// ---- dates (local calendar days, YYYY-MM-DD) ----------------------------------
export function isoDay(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

export function parseDay(s: string): Date {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, (m || 1) - 1, d || 1);
}

export function addDays(s: string, n: number): string {
  const d = parseDay(s);
  d.setDate(d.getDate() + n);
  return isoDay(d);
}

export function mondayOf(s: string): string {
  const d = parseDay(s);
  const shift = (d.getDay() + 6) % 7; // Mon=0 … Sun=6
  d.setDate(d.getDate() - shift);
  return isoDay(d);
}

export const today = () => isoDay(new Date());
export const tomorrow = () => addDays(today(), 1);

export function dayLabel(s: string): string {
  return parseDay(s).toLocaleDateString("ru-RU", { weekday: "short", day: "numeric", month: "short" });
}
