// Advertising helpers shared by the ads pages: labels, file urls, upload, template checks.
import { ApiError } from "./api";
import type { Advertiser, CampaignStatus, Placement } from "./types/ads";

export const PLACEHOLDERS = ["brand", "product", "cta", "legal_name", "must", "must_not", "link", "promo_code"] as const;

export const PLACEHOLDER_LABEL: Record<(typeof PLACEHOLDERS)[number], string> = {
  brand: "бренд",
  product: "продукт кампании",
  cta: "призыв",
  legal_name: "юрлицо",
  must: "обязательные фразы",
  must_not: "запреты",
  link: "ссылка",
  promo_code: "промокод",
};

export const PLACEMENT_LABEL: Record<Placement, string> = {
  plot: "Продукт в сюжете",
  hero_uses_product: "Герой пользуется продуктом",
  packshot: "Пэкшот в конце",
  caption_comment: "Только подпись / комментарий",
};

export const CAMPAIGN_STATUS_LABEL: Record<CampaignStatus, string> = {
  draft: "Черновик",
  active: "Идёт",
  done: "Завершена",
};

export const advFileUrl = (adv: Pick<Advertiser, "id">, rel: string, w?: number) =>
  `/api/advertisers/${encodeURIComponent(adv.id)}/file/${rel.split("/").map(encodeURIComponent).join("/")}${w ? `?w=${w}` : ""}`;

export const MAX_UPLOAD = 15 * 1024 * 1024;

/** multipart upload (api.ts sends JSON only). */
export async function uploadAdvertiserFile(advId: string, kind: "logo" | "product", file: File): Promise<Advertiser> {
  if (file.size > MAX_UPLOAD) throw new ApiError(413, `${file.name}: больше 15 МБ`);
  const form = new FormData();
  form.append("kind", kind);
  form.append("file", file);
  const res = await fetch(`/api/advertisers/${encodeURIComponent(advId)}/files`, {
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
  return (await res.json()).advertiser;
}

/** Unknown placeholders in a template (the server rejects them with 422 too). */
export function unknownPlaceholders(template: string): string[] {
  const stripped = template.replace(/\{\{|\}\}/g, "");
  const found = [...stripped.matchAll(/\{([^{}]*)\}/g)].map((m) => m[1]);
  return [...new Set(found.filter((n) => !(PLACEHOLDERS as readonly string[]).includes(n)))];
}

export const lines = (text: string) => text.split("\n").map((s) => s.trim()).filter(Boolean);

export const unixDate = (ts: number | null | undefined) =>
  ts ? new Date(ts * 1000).toLocaleString("ru-RU", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";

export const isoDate = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleDateString("ru-RU", { day: "2-digit", month: "short", year: "numeric" }) : "—";
