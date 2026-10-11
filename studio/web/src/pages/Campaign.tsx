import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { Card, Empty, ErrorNote, StateBadge } from "../components/ui";
import { CAMPAIGN_STATUS_LABEL, isoDate, PLACEMENT_LABEL, unixDate } from "../ads";
import { isHttpUrl, shortId } from "../format";
import type {
  Advertiser,
  Campaign,
  CampaignBriefsResult,
  CampaignInput,
  CampaignReport,
  CampaignStatus,
  Insert,
  InsertVersion,
  Publication,
} from "../types/ads";
import "../ads.css";

interface Draft {
  name: string;
  advertiser_id: string;
  insert_id: string;
  insert_version: number;
  erid: string;
  erid_not_required: boolean;
  product: string;
  period_start: string;
  period_end: string;
  planned_videos: number;
  accounts: string;
  status: CampaignStatus;
}

const toDraft = (c: Campaign): Draft => ({
  name: c.name,
  advertiser_id: c.advertiser_id,
  insert_id: c.insert_id,
  insert_version: c.insert_version,
  erid: c.erid,
  erid_not_required: c.erid_not_required,
  product: c.product,
  period_start: c.period_start ?? "",
  period_end: c.period_end ?? "",
  planned_videos: c.planned_videos,
  accounts: c.accounts.join(", "),
  status: c.status,
});

const toInput = (d: Draft): CampaignInput => ({
  ...d,
  period_start: d.period_start || null,
  period_end: d.period_end || null,
  accounts: d.accounts.split(/[\s,]+/).filter(Boolean),
});

const PUB_LABEL: Record<Publication["status"], string> = {
  pending: "ждёт",
  running: "публикуется",
  done: "опубликовано",
  failed: "ошибка",
  cancelled: "отменено",
};

function Settings({ campaign }: { campaign: Campaign }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const advertisers = useQuery({ queryKey: ["advertisers", true], queryFn: () => api.get<Advertiser[]>("/advertisers?archived=true") });
  const inserts = useQuery({ queryKey: ["inserts", true], queryFn: () => api.get<Insert[]>("/inserts?archived=true") });
  const [d, setD] = useState<Draft>(toDraft(campaign));
  useEffect(() => setD(toDraft(campaign)), [campaign]);
  const versions = useQuery({
    queryKey: ["insert-versions", d.insert_id],
    queryFn: () => api.get<InsertVersion[]>(`/inserts/${encodeURIComponent(d.insert_id)}/versions`),
    enabled: !!d.insert_id,
  });

  const save = useMutation({
    mutationFn: () => api.patch<Campaign>(`/campaigns/${encodeURIComponent(campaign.id)}`, toInput(d)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["campaign", campaign.id] });
      qc.invalidateQueries({ queryKey: ["campaigns"] });
      qc.invalidateQueries({ queryKey: ["campaign-report", campaign.id] });
    },
  });
  const remove = useMutation({
    mutationFn: () => api.del(`/campaigns/${encodeURIComponent(campaign.id)}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["campaigns"] });
      nav("/campaigns");
    },
  });
  const dirty = JSON.stringify(d) !== JSON.stringify(toDraft(campaign));
  const pinned = versions.data?.find((v) => v.version === d.insert_version);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };

  return (
    <Card title="Настройки">
      <form className="form" onSubmit={submit}>
        <label className="span-2">
          Название
          <input value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} />
        </label>
        <label>
          Рекламодатель
          <select value={d.advertiser_id} onChange={(e) => setD({ ...d, advertiser_id: e.target.value })}>
            {(advertisers.data ?? []).map((a) => (
              <option key={a.id} value={a.id}>
                {a.brand}
                {a.archived ? " (архив)" : ""}
              </option>
            ))}
          </select>
        </label>
        <label>
          Продукт (что рекламируем)
          <input value={d.product} onChange={(e) => setD({ ...d, product: e.target.value })} placeholder="Пицца «Маргарита»" />
        </label>
        <label>
          Шаблон вставки
          <select
            value={d.insert_id}
            onChange={(e) => {
              const ins = inserts.data?.find((i) => i.id === e.target.value);
              setD({ ...d, insert_id: e.target.value, insert_version: ins?.current_version ?? 1 });
            }}
          >
            {(inserts.data ?? []).map((i) => (
              <option key={i.id} value={i.id}>
                {i.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Закреплённая версия
          <select value={d.insert_version} onChange={(e) => setD({ ...d, insert_version: Number(e.target.value) })}>
            {(versions.data ?? []).map((v) => (
              <option key={v.version} value={v.version}>
                v{v.version} · {unixDate(v.created_at)}
              </option>
            ))}
          </select>
        </label>
        {pinned && (
          <p className="span-2 muted">
            {PLACEMENT_LABEL[pinned.placement]}, до {Math.round(pinned.max_screen_share * 100)}% экрана ·{" "}
            <Link to={`/inserts/${d.insert_id}`}>открыть шаблон</Link>
          </p>
        )}
        <label>
          ERID (токен ОРД)
          <input value={d.erid} disabled={d.erid_not_required} onChange={(e) => setD({ ...d, erid: e.target.value })} />
        </label>
        <label className="ads-check">
          <input
            type="checkbox"
            checked={d.erid_not_required}
            onChange={(e) => setD({ ...d, erid_not_required: e.target.checked, erid: e.target.checked ? "" : d.erid })}
          />
          ERID не требуется
        </label>
        <label>
          Начало
          <input type="date" value={d.period_start} onChange={(e) => setD({ ...d, period_start: e.target.value })} />
        </label>
        <label>
          Конец
          <input type="date" value={d.period_end} onChange={(e) => setD({ ...d, period_end: e.target.value })} />
        </label>
        <label>
          Роликов по плану
          <input
            type="number"
            min={0}
            value={d.planned_videos}
            onChange={(e) => setD({ ...d, planned_videos: Math.max(0, Number(e.target.value) || 0) })}
          />
        </label>
        <label>
          Статус
          <select value={d.status} onChange={(e) => setD({ ...d, status: e.target.value as CampaignStatus })}>
            {Object.entries(CAMPAIGN_STATUS_LABEL).map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <label className="span-2">
          Аккаунты для публикации (через запятую)
          <input value={d.accounts} onChange={(e) => setD({ ...d, accounts: e.target.value })} placeholder="ig_kot, tt_kot" />
        </label>
        {campaign.marking && (
          <div className="span-2 ads-marking">
            Маркировка в подписи: <strong>{campaign.marking}</strong>
          </div>
        )}
        {!campaign.erid && !campaign.erid_not_required && (
          <p className="span-2 ads-warn">ERID не указан — ролики можно делать, но перед публикацией его нужно вписать.</p>
        )}
        <div className="span-2 form-foot">
          <ErrorNote error={save.error || remove.error} />
          {campaign.status === "draft" && (
            <button
              type="button"
              className="btn btn-danger"
              disabled={remove.isPending}
              onClick={() => confirm("Удалить черновик кампании?") && remove.mutate()}
            >
              Удалить
            </button>
          )}
          <button className="btn btn-primary" disabled={!dirty || !d.name.trim() || save.isPending}>
            {save.isPending ? "Сохраняю…" : "Сохранить"}
          </button>
        </div>
      </form>
    </Card>
  );
}

function CreateBriefs({ campaign }: { campaign: Campaign }) {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [count, setCount] = useState(1);
  const create = useMutation({
    mutationFn: () =>
      api.post<CampaignBriefsResult>(`/campaigns/${encodeURIComponent(campaign.id)}/briefs`, { text, count }),
    onSuccess: () => {
      setText("");
      qc.invalidateQueries({ queryKey: ["briefs"] });
      qc.invalidateQueries({ queryKey: ["overview"] });
      qc.invalidateQueries({ queryKey: ["campaign-report", campaign.id] });
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };
  if (campaign.status === "done") return null;
  return (
    <Card title="Создать брифы">
      <form className="form" onSubmit={submit}>
        <label className="span-2">
          Идея ролика (рекламные данные подставятся из кампании)
          <textarea
            rows={4}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Кот-повар спасает вечеринку: гости голодные, пицца приезжает за 30 минут. 30 секунд."
          />
        </label>
        <label>
          Сколько брифов
          <input type="number" min={1} max={20} value={count} onChange={(e) => setCount(Math.min(20, Math.max(1, Number(e.target.value) || 1)))} />
        </label>
        <div className="form-foot">
          <ErrorNote error={create.error} />
          <button className="btn btn-primary" disabled={text.trim().length < 10 || create.isPending}>
            {create.isPending ? "Ставлю…" : "Поставить в очередь"}
          </button>
        </div>
        {create.data && (
          <div className="span-2">
            <p className="muted">
              В очереди:{" "}
              {create.data.ids.map((id, n) => (
                <span key={id}>
                  {n > 0 && ", "}
                  <Link to={`/briefs/${id}`}>{shortId(id)}</Link>
                </span>
              ))}
            </p>
            {create.data.warnings.map((w) => (
              <p key={w} className="ads-warn">
                ⚠ {w}
              </p>
            ))}
          </div>
        )}
      </form>
    </Card>
  );
}

function Report({ campaign }: { campaign: Campaign }) {
  const { data, error } = useQuery({
    queryKey: ["campaign-report", campaign.id],
    queryFn: () => api.get<CampaignReport>(`/campaigns/${encodeURIComponent(campaign.id)}/report`),
  });
  return (
    <Card
      title="Отчёт для клиента"
      actions={
        <a className="btn" href={`/api/campaigns/${encodeURIComponent(campaign.id)}/report.csv`} download>
          Скачать CSV
        </a>
      }
    >
      <ErrorNote error={error} />
      {data && (
        <>
          <div className="ads-totals">
            <span>
              <strong>{data.totals.briefs}</strong>брифов
            </span>
            <span>
              <strong>
                {data.totals.approved} / {data.campaign.planned_videos}
              </strong>
              одобрено / план
            </span>
            <span>
              <strong>{data.totals.publications}</strong>публикаций
            </span>
            <span>
              <strong>{data.advertiser.inn || "—"}</strong>ИНН {data.advertiser.legal_name}
            </span>
            <span>
              <strong>
                {isoDate(data.campaign.period_start)} — {isoDate(data.campaign.period_end)}
              </strong>
              период
            </span>
          </div>
          {data.rows.length === 0 ? (
            <Empty>Брифов по кампании пока нет</Empty>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Ролик</th>
                    <th>Состояние</th>
                    <th className="hide-mobile">Одобрен</th>
                    <th>Публикации</th>
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((r) => (
                    <tr key={r.brief_id}>
                      <td>
                        <Link to={`/briefs/${r.brief_id}`}>{shortId(r.brief_id)}</Link>
                        {r.caption && <div className="muted clamp">{r.caption}</div>}
                        {(r.state === "awaiting_approval" || r.state === "approved") && (
                          <div>
                            <Link to={`/videos?open=${encodeURIComponent(r.brief_id)}`}>смотреть видео</Link>
                          </div>
                        )}
                      </td>
                      <td>
                        <StateBadge state={r.state} />
                      </td>
                      <td className="hide-mobile muted nowrap">{isoDate(r.approved_at)}</td>
                      <td>
                        {r.publications.length === 0 ? (
                          <span className="muted">—</span>
                        ) : (
                          <ul className="ads-pubs">
                            {r.publications.map((p) => (
                              <li key={p.job_id}>
                                {p.account} · {PUB_LABEL[p.status]}
                                {p.finished_at ? ` · ${unixDate(p.finished_at)}` : ""}
                                {isHttpUrl(p.post_url) && (
                                  <>
                                    {" · "}
                                    <a href={p.post_url} target="_blank" rel="noreferrer">
                                      пост
                                    </a>
                                  </>
                                )}
                              </li>
                            ))}
                          </ul>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </Card>
  );
}

export function CampaignPage() {
  const { id = "" } = useParams();
  const { data: campaign, error } = useQuery({
    queryKey: ["campaign", id],
    queryFn: () => api.get<Campaign>(`/campaigns/${encodeURIComponent(id)}`),
  });
  if (error) return <div className="page"><ErrorNote error={error} /></div>;
  if (!campaign) return <div className="page muted">Загрузка…</div>;
  return (
    <div className="page">
      <header className="page-head">
        <div>
          <Link to="/campaigns" className="muted">← Кампании</Link>
          <h1>{campaign.name}</h1>
          <span className="muted">{CAMPAIGN_STATUS_LABEL[campaign.status]}</span>
        </div>
        <Link className="btn btn-ghost" to={`/advertisers/${campaign.advertiser_id}`}>
          Рекламодатель
        </Link>
      </header>
      <Settings campaign={campaign} />
      <CreateBriefs campaign={campaign} />
      <Report campaign={campaign} />
    </div>
  );
}
