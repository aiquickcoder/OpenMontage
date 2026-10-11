import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { Card, Empty, ErrorNote } from "../components/ui";
import { CAMPAIGN_STATUS_LABEL, isoDate } from "../ads";
import type { Advertiser, Campaign, CampaignStatus, Insert } from "../types/ads";
import "../ads.css";

function NewCampaign() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const advertisers = useQuery({ queryKey: ["advertisers", false], queryFn: () => api.get<Advertiser[]>("/advertisers") });
  const inserts = useQuery({ queryKey: ["inserts", false], queryFn: () => api.get<Insert[]>("/inserts") });
  const [name, setName] = useState("");
  const [advId, setAdvId] = useState("");
  const [insId, setInsId] = useState("");
  const create = useMutation({
    mutationFn: () => api.post<Campaign>("/campaigns", { name, advertiser_id: advId, insert_id: insId }),
    onSuccess: (c) => {
      qc.invalidateQueries({ queryKey: ["campaigns"] });
      nav(`/campaigns/${c.id}`);
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };
  return (
    <Card title="Новая кампания">
      <form className="form" onSubmit={submit}>
        <label className="span-2">
          Название
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Корочка — октябрь" />
        </label>
        <label>
          Рекламодатель
          <select value={advId} onChange={(e) => setAdvId(e.target.value)}>
            <option value="">— выберите —</option>
            {(advertisers.data ?? []).map((a) => (
              <option key={a.id} value={a.id}>
                {a.brand}
              </option>
            ))}
          </select>
        </label>
        <label>
          Шаблон вставки
          <select value={insId} onChange={(e) => setInsId(e.target.value)}>
            <option value="">— выберите —</option>
            {(inserts.data ?? []).map((i) => (
              <option key={i.id} value={i.id}>
                {i.name} (v{i.version})
              </option>
            ))}
          </select>
        </label>
        {(advertisers.data?.length === 0 || inserts.data?.length === 0) && (
          <p className="span-2 muted">
            Сначала создайте <Link to="/advertisers">рекламодателя</Link> и <Link to="/inserts">шаблон вставки</Link>.
          </p>
        )}
        <div className="span-2 form-foot">
          <ErrorNote error={create.error} />
          <button className="btn btn-primary" disabled={!name.trim() || !advId || !insId || create.isPending}>
            {create.isPending ? "Создаю…" : "Создать"}
          </button>
        </div>
      </form>
    </Card>
  );
}

const FILTERS: (CampaignStatus | "")[] = ["", "draft", "active", "done"];

export function CampaignsPage() {
  const [status, setStatus] = useState<CampaignStatus | "">("");
  const { data, error } = useQuery({ queryKey: ["campaigns"], queryFn: () => api.get<Campaign[]>("/campaigns") });
  const rows = (data ?? []).filter((c) => !status || c.status === status);
  return (
    <div className="page">
      <header className="page-head">
        <h1>Кампании</h1>
      </header>
      <NewCampaign />
      <Card
        title="Кампании"
        actions={
          <div className="chips">
            {FILTERS.map((f) => (
              <button key={f || "all"} className={`chip${status === f ? " chip-on" : ""}`} onClick={() => setStatus(f)}>
                {f ? CAMPAIGN_STATUS_LABEL[f] : "Все"}
              </button>
            ))}
          </div>
        }
      >
        <ErrorNote error={error} />
        {data && rows.length === 0 && <Empty>Кампаний нет</Empty>}
        {rows.length > 0 && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Кампания</th>
                  <th>Статус</th>
                  <th className="hide-mobile">Период</th>
                  <th className="hide-mobile">План</th>
                  <th className="hide-mobile">ERID</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((c) => (
                  <tr key={c.id}>
                    <td>
                      <Link to={`/campaigns/${c.id}`}>{c.name}</Link>
                      <div className="muted">{c.advertiser_brand}</div>
                    </td>
                    <td>{CAMPAIGN_STATUS_LABEL[c.status]}</td>
                    <td className="hide-mobile muted nowrap">
                      {isoDate(c.period_start)} — {isoDate(c.period_end)}
                    </td>
                    <td className="hide-mobile">{c.planned_videos}</td>
                    <td className="hide-mobile nowrap">
                      {c.erid ? c.erid : c.erid_not_required ? <span className="muted">не нужен</span> : <span className="ads-warn">нет</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
