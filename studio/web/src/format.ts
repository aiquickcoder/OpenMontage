import type { BriefState } from "./api";

export const STATE_LABEL: Record<BriefState, string> = {
  queued: "В очереди",
  running: "Делается",
  awaiting_approval: "Ждёт одобрения",
  approved: "Одобрен",
  rejected: "Отклонён",
  failed: "Ошибка",
  budget: "Нет кредитов",
};

export const STAGE_LABEL: Record<string, string> = {
  idea: "Идея",
  proposal: "Концепт",
  script: "Сценарий",
  character_design: "Персонажи",
  scene_plan: "Раскадровка",
  assets: "Генерация",
  edit: "Монтаж",
  compose: "Сборка",
  publish: "Публикация",
};

const rtf = new Intl.RelativeTimeFormat("ru", { numeric: "auto", style: "short" });

export function ago(iso: string): string {
  const diff = (new Date(iso).getTime() - Date.now()) / 1000;
  const abs = Math.abs(diff);
  if (abs < 60) return rtf.format(Math.round(diff), "second");
  if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
  return rtf.format(Math.round(diff / 86400), "day");
}

export function dateTime(iso: string): string {
  return new Date(iso).toLocaleString("ru-RU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function shortId(id: string): string {
  // 20261011-071918-kot-povar… → kot-povar… (the date is shown separately)
  return id.replace(/^\d{8}-\d{6}-/, "");
}
