// Phone farm helpers shared by the farm pages: labels, time formatting, status/command hooks.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import type { CommandResult, FarmStatus, JobStatus, Platform } from "./types/farm";

export const PLATFORM_LABEL: Record<Platform, string> = {
  instagram: "Instagram",
  tiktok: "TikTok",
  youtube: "YouTube",
  vk: "VK",
};

export const PLATFORM_SHORT: Record<Platform, string> = { instagram: "IG", tiktok: "TT", youtube: "YT", vk: "VK" };

export const JOB_STATUS_LABEL: Record<JobStatus, string> = {
  pending: "В очереди",
  running: "Публикуется",
  done: "Опубликовано",
  failed: "Ошибка",
  cancelled: "Отменено",
};

export const MODE_LABEL: Record<string, string> = { warmup: "Прогрев", audience: "Аудитория" };

export const ADB_LABEL: Record<string, string> = {
  device: "подключён",
  offline: "офлайн",
  unauthorized: "нет доступа (подтвердите отладку)",
  missing: "не найден",
};

const rtf = new Intl.RelativeTimeFormat("ru", { numeric: "auto", style: "short" });

/** Unix seconds -> "5 мин. назад" / "через 2 ч". */
export function agoTs(ts: number | null | undefined): string {
  if (!ts) return "—";
  const diff = ts - Date.now() / 1000;
  const abs = Math.abs(diff);
  if (abs < 60) return rtf.format(Math.round(diff), "second");
  if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
  return rtf.format(Math.round(diff / 86400), "day");
}

export function timeTs(ts: number | null | undefined): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString("ru-RU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function useFarmStatus() {
  return useQuery({
    queryKey: ["farm-status"],
    queryFn: () => api.get<FarmStatus>("/farm/status"),
    refetchInterval: 15_000,
  });
}

/** Refresh every farm view after a command (the agent answers with a new snapshot shortly). */
export function useFarmRefresh() {
  const qc = useQueryClient();
  return () => {
    for (const key of ["farm-status", "devices", "accounts", "publications"]) qc.invalidateQueries({ queryKey: [key] });
    // the snapshot follows the ack by a moment; look again a bit later
    window.setTimeout(() => {
      for (const key of ["devices", "accounts", "publications"]) qc.invalidateQueries({ queryKey: [key] });
    }, 2500);
  };
}

/** Human-readable outcome of a command response. */
export function commandNote(res: CommandResult, doneText = "Готово"): string {
  if (res.status === "done") return doneText;
  if (res.status === "failed") return `Ферма отклонила: ${res.error ?? "без описания"}`;
  return "Команда в очереди — выполнится, когда ферма ответит";
}

export function splitList(text: string): string[] {
  return text.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
}
