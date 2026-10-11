// Advertising types (mirror studio/ads.py).
import type { BriefState } from "../api";

export type Placement = "plot" | "hero_uses_product" | "packshot" | "caption_comment";
export type CampaignStatus = "draft" | "active" | "done";

export interface Advertiser {
  id: string;
  brand: string;
  legal_name: string;
  inn: string;
  link: string;
  promo_code: string;
  cta: string;
  must_say: string[];
  must_not_say: string[];
  notes: string;
  archived: boolean;
  logo: string | null;      // "logo.png", relative to the advertiser folder
  products: string[];       // "products/x.jpg"
  created_at: number;
  updated_at: number;
}

export type AdvertiserInput = Partial<Omit<Advertiser, "id" | "logo" | "products" | "created_at" | "updated_at">>;

export interface Insert {
  id: string;
  version: number;
  current_version: number;
  name: string;
  placement: Placement;
  max_screen_share: number;  // 0..0.5
  template: string;
  placeholders: string[];
  archived: boolean;
  created_at: number;
  updated_at: number;
}

export interface InsertVersion {
  insert_id: string;
  version: number;
  name: string;
  placement: Placement;
  max_screen_share: number;
  template: string;
  created_at: number;
}

export type InsertInput = Partial<Pick<Insert, "name" | "placement" | "max_screen_share" | "template" | "archived">>;

export interface InsertPreview { text: string; version: number; marking: string }

export interface Campaign {
  id: string;
  name: string;
  advertiser_id: string;
  insert_id: string;
  insert_version: number;
  erid: string;
  erid_not_required: boolean;
  product: string;
  period_start: string | null;  // YYYY-MM-DD
  period_end: string | null;
  planned_videos: number;
  accounts: string[];
  status: CampaignStatus;
  created_at: number;
  updated_at: number;
  advertiser_brand?: string;    // list only
  marking?: string | null;      // detail only
}

export type CampaignInput = Partial<Omit<Campaign, "id" | "created_at" | "updated_at" | "advertiser_brand" | "marking">>;

export interface Publication {
  job_id: number;
  account: string;
  device: string;
  status: "pending" | "running" | "done" | "failed" | "cancelled";
  finished_at: number | null;
  post_url: string | null;
}

export interface ReportRow {
  brief_id: string;
  state: BriefState;
  created_at: string;
  approved_at: string | null;
  caption: string;
  publications: Publication[];
}

export interface CampaignReport {
  campaign: Pick<Campaign, "id" | "name" | "status" | "erid" | "erid_not_required" | "period_start" | "period_end" | "planned_videos" | "product">;
  advertiser: { id: string | null; brand: string; legal_name: string; inn: string };
  marking: string | null;
  totals: { briefs: number; approved: number; publications: number };
  rows: ReportRow[];
}

export interface CampaignBriefsResult { ids: string[]; warnings: string[] }
