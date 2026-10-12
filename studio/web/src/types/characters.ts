// Character library types + helpers (mirror studio/characters.py).
import { ApiError } from "../api";

export type CharacterRole = "hero" | "sidekick" | "antagonist" | "extra" | "mascot";
export type ImageKind = "turnaround" | "expression" | "ref";
export type TaskStatus = "queued" | "running" | "done" | "failed";

export interface CharacterVoice { language: string; description: string }

export interface CharacterLibraryMeta {
  style_lock: string;
  tags: string[];
  created_at: string;
  updated_at: string;
  source: string; // "manual" | "generated" | "project:<id>"
  archived: boolean;
  task_id?: string;
  world_id?: string; // series world this character belongs to
}

export interface TaskBrief { id: string; status: TaskStatus; error: string | null; updated_at: string }

export interface Character {
  id: string;
  name: string;
  role: string;
  description: string;
  turnaround_path: string; // relative to the character dir; "" until a sheet exists
  expression_sheet_path?: string;
  flow_media_ids?: string[];
  voice: CharacterVoice;
  prompt_tokens: string;
  library: CharacterLibraryMeta;
  task: TaskBrief | null;
  files?: { refs: string[]; variants: string[] }; // detail only
}

export interface CharacterInput {
  name: string;
  role: string;
  description: string;
  prompt_tokens?: string;
  voice: CharacterVoice;
  style_lock?: string;
  tags?: string[];
}

export interface GenerateInput extends CharacterInput { variants: number }
export interface GenerateResult { id: string; task_id: string; character: Character }

export interface CharacterTask {
  id: string;
  kind: "character_generate";
  args: { character_id: string; variants: number };
  status: TaskStatus;
  result: { variants: string[]; media_ids: string[] } | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export const ROLE_LABEL: Record<CharacterRole, string> = {
  hero: "Герой",
  sidekick: "Напарник",
  antagonist: "Антагонист",
  extra: "Эпизод",
  mascot: "Маскот",
};

export const TASK_LABEL: Record<TaskStatus, string> = {
  queued: "В очереди",
  running: "Рисуется",
  done: "Готово",
  failed: "Ошибка",
};

export const charFileUrl = (id: string, rel: string, w?: number) =>
  `/api/characters/${encodeURIComponent(id)}/file/${rel.split("/").map(encodeURIComponent).join("/")}${w ? `?w=${w}` : ""}`;

/** Multipart upload (api.ts only speaks JSON). */
export async function uploadCharacterImage(id: string, kind: ImageKind, file: File): Promise<{ path: string; character: Character }> {
  const form = new FormData();
  form.append("kind", kind);
  form.append("file", file);
  const res = await fetch(`/api/characters/${encodeURIComponent(id)}/image`, {
    method: "POST",
    headers: { "X-Studio": "1" },
    credentials: "same-origin",
    body: form,
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
  return res.json();
}
