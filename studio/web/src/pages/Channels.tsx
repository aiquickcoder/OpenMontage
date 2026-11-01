import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { Card, Empty, ErrorNote } from "../components/ui";
import "../channels.css";

// ---- types (mirror studio/channels.py) --------------------------------------
interface Channel {
  key: string;
  username: string;
  title: string;
  per_day: number;
  quiet_from: number;
  quiet_to: number;
  min_gap_min: number;
  min_score: number;
  max_age_h: number;
  paused: number;
  mode: "trial" | "live";
  bot_env: string;
  cta: string;
  style: string;
  note: string;
  hours: number[];
  today: Record<string, number>;
  queue: number;
  out_today: number;
  owed_now: number;
  rights: { ok?: boolean; bot?: string; status?: string; detail?: string };
}
interface Usage { model: string; purpose: string; calls: number; tokens_in: number; tokens_out: number; cost: number }
interface Overview {
  channels: Channel[];
  usage: Usage[];
  spent_today: number;
  budget: number;
  alive: Record<string, number | null>;
  sources: { n: number; e: number; j: number };
  posts_last_hour: number;
  reader_channels: number | null;
  events: { ts: number; kind: string; text: string }[];
}
interface Post {
  id: number;
  story_id: number;
  channel: string;
  status: string;
  html: string;
  score: number;
  urgent: number;
  title: string;
  media_note: string;
  media_count: number;
  sources: string[];
  error: string;
  created: number;
  published_at: number | null;
  message_ids: number[];
}
interface Story {
  id: number;
  first_ts: number;
  title: string;
  lead: string;
  region: string;
  category: string;
  n_posts: number;
  n_channels: number;
  status: string;
  score: number;
  reason: string;
}
interface Source {
  id: number;
  platform: string;
  ref: string;
  title: string;
  region: string;
  kind: string;
  trust: number;
  enabled: number;
  every_min: number;
  joined: number;
  last_read: number;
  posts_seen: number;
  error: string;
  note: string;
}

const STATUS_LABEL: Record<string, string> = {
  draft: "пишется", media: "медиа", ready: "в очереди", previewed: "превью", published: "опубликован",
  deleted: "удалён", rejected: "отклонён", expired: "устарел", failed: "сбой",
};
const KIND_LABEL: Record<string, string> = { aggregator: "агрегатор", city: "городской", primary: "первоисточник", media: "СМИ" };
const REGION_LABEL: Record<string, string> = { russia: "Россия", moscow: "Москва", kazan: "Казань", world: "мир" };

const time = (ts?: number | null) =>
  ts ? new Date(ts * 1000).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—";

/** Telegram HTML as the engine stores it: escaped text plus <b>, <i> and t.me links only. Anything else is shown
 *  as text. */
function safeHtml(html: string): string {
  return (html || "")
    .replace(/<(?!\/?[bi]>|a href="https:\/\/t\.me\/[A-Za-z0-9_]+">|\/a>)/g, "&lt;")
    .replace(/\n/g, "<br>");
}

// ---- channels ---------------------------------------------------------------
function ChannelCard({ ch }: { ch: Channel }) {
  const qc = useQueryClient();
  const [form, setForm] = useState({
    per_day: ch.per_day, quiet_from: ch.quiet_from, quiet_to: ch.quiet_to, min_gap_min: ch.min_gap_min,
    min_score: ch.min_score, max_age_h: ch.max_age_h, cta: ch.cta, style: ch.style,
  });
  useEffect(() => {
    setForm({ per_day: ch.per_day, quiet_from: ch.quiet_from, quiet_to: ch.quiet_to, min_gap_min: ch.min_gap_min,
      min_score: ch.min_score, max_age_h: ch.max_age_h, cta: ch.cta, style: ch.style });
  }, [ch.per_day, ch.quiet_from, ch.quiet_to, ch.min_gap_min, ch.min_score, ch.max_age_h, ch.cta, ch.style]);
  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.patch<Channel>(`/channels/ch/${ch.key}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ch-overview"] }),
  });
  const num = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [k]: Number(e.target.value) });
  const save = (e: FormEvent) => {
    e.preventDefault();
    patch.mutate(form);
  };
  const t = ch.today;
  return (
    <Card
      title={
        <>
          <a href={`https://t.me/${ch.username}`} target="_blank" rel="noreferrer">@{ch.username}</a>{" "}
          <span className={`badge ${ch.mode === "live" ? "badge-approved" : "badge-awaiting_approval"}`}>
            {ch.mode === "live" ? "публикует" : "пробный режим"}
          </span>{" "}
          {ch.paused ? <span className="badge badge-failed">пауза</span> : null}
        </>
      }
      actions={
        <div className="chips">
          <button className="btn" disabled={patch.isPending} onClick={() => patch.mutate({ paused: !ch.paused })}>
            {ch.paused ? "▶️ Возобновить" : "⏸ Пауза"}
          </button>
          <button
            className={`btn ${ch.mode === "live" ? "btn-ghost" : "btn-primary"}`}
            disabled={patch.isPending}
            onClick={() => {
              const live = ch.mode !== "live";
              if (!live || confirm(`Включить живую публикацию в @${ch.username}? Первые сутки — половина частоты.`))
                patch.mutate({ mode: live ? "live" : "trial" });
            }}
          >
            {ch.mode === "live" ? "В пробный режим" : "Включить публикацию"}
          </button>
        </div>
      }
    >
      <ErrorNote error={patch.error} />
      {ch.note && <div className="detail-note">{ch.note}</div>}
      <div className="ch-stats">
        <span>сегодня вышло <b>{ch.out_today}</b> из <b>{ch.owed_now}</b> к этому часу (цель {ch.per_day}/день)</span>
        <span>в очереди <b>{ch.queue}</b></span>
        <span className="muted">
          отклонено {t.rejected ?? 0} · устарело {t.expired ?? 0} · сбоев {t.failed ?? 0}
        </span>
        {ch.rights?.bot && (
          <span className={ch.rights.ok ? "ok" : "bad"}>
            бот @{ch.rights.bot}: {ch.rights.ok ? "может публиковать" : ch.rights.detail || ch.rights.status}
          </span>
        )}
      </div>
      <form className="form ch-form" onSubmit={save}>
        <label>Постов в день<input type="number" step="1" min="0" value={form.per_day} onChange={num("per_day")} /></label>
        <label>Тихие часы с<input type="number" min="0" max="23" value={form.quiet_from} onChange={num("quiet_from")} /></label>
        <label>до<input type="number" min="0" max="23" value={form.quiet_to} onChange={num("quiet_to")} /></label>
        <label>Интервал, мин<input type="number" min="1" value={form.min_gap_min} onChange={num("min_gap_min")} /></label>
        <label>Порог оценки<input type="number" step="0.1" value={form.min_score} onChange={num("min_score")} /></label>
        <label>Свежесть, ч<input type="number" step="1" value={form.max_age_h} onChange={num("max_age_h")} /></label>
        <label className="span-2">Подпись-ссылка в конце<input value={form.cta} onChange={(e) => setForm({ ...form, cta: e.target.value })} /></label>
        <label className="span-2">
          Дополнительно к стайлгайду этого канала
          <textarea rows={2} value={form.style} onChange={(e) => setForm({ ...form, style: e.target.value })} />
        </label>
        <div className="form-foot">
          <button className="btn btn-primary" disabled={patch.isPending}>Сохранить</button>
        </div>
      </form>
      <HoursBar hours={ch.hours} />
    </Card>
  );
}

function HoursBar({ hours }: { hours: number[] }) {
  const max = Math.max(...hours, 0.01);
  return (
    <div className="hours" title="Профиль публикаций по часам (МСК), снят с каналов-ориентиров">
      {hours.map((h, i) => (
        <div key={i} className="hour">
          <div className="hour-bar" style={{ height: `${Math.round((h / max) * 100)}%` }} />
          <span>{i}</span>
        </div>
      ))}
    </div>
  );
}

function OverviewTab({ data }: { data: Overview }) {
  return (
    <>
      {data.channels.map((ch) => <ChannelCard key={ch.key} ch={ch} />)}
      <Card title="Движок">
        <div className="ch-stats">
          {Object.entries(data.alive).map(([k, v]) => (
            <span key={k} className={v !== null && v < 180 ? "ok" : "bad"}>
              {k}: {v === null ? "не запущен" : `${v} с назад`}
            </span>
          ))}
        </div>
        <div className="ch-stats">
          <span>источников {data.sources.n} (включено {data.sources.e}, аккаунт вступил в {data.sources.j ?? 0}
            {data.reader_channels ? `, всего каналов у аккаунта ${data.reader_channels}` : ""})</span>
          <span>постов прочитано за час: {data.posts_last_hour}</span>
          <span>ИИ сегодня: ${data.spent_today.toFixed(3)} из ${data.budget}</span>
        </div>
        {data.usage.length > 0 && (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Задача</th><th>Модель</th><th>Вызовов</th><th>Токены</th><th>$</th></tr></thead>
              <tbody>
                {data.usage.map((u) => (
                  <tr key={u.model + u.purpose}>
                    <td>{u.purpose}</td><td>{u.model}</td><td>{u.calls}</td>
                    <td>{u.tokens_in.toLocaleString("ru-RU")} / {u.tokens_out.toLocaleString("ru-RU")}</td>
                    <td>{u.cost.toFixed(4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <ul className="events">
          {data.events.map((e, i) => <li key={i}><span className="muted">{time(e.ts)} {e.kind}</span> {e.text}</li>)}
        </ul>
      </Card>
    </>
  );
}

// ---- posts ------------------------------------------------------------------
const POST_FILTERS: [string, string][] = [
  ["ready,media,draft", "Очередь"], ["published,previewed", "Вышли"], ["rejected,expired,failed,deleted", "Отсев"], ["", "Все"],
];

function PostsTab({ channels }: { channels: Channel[] }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState(POST_FILTERS[0][0]);
  const [channel, setChannel] = useState("");
  const { data, error } = useQuery({
    queryKey: ["ch-posts", status, channel],
    queryFn: () => api.get<Post[]>(`/channels/posts?status=${status}&channel=${channel}&limit=80`),
    refetchInterval: 20000,
  });
  const del = useMutation({
    mutationFn: (id: number) => api.post<{ note: string }>(`/channels/posts/${id}/delete`),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["ch-posts"] }); alert(r.note); },
  });
  const user = Object.fromEntries(channels.map((c) => [c.key, c.username]));
  return (
    <Card
      title="Посты"
      actions={
        <div className="chips">
          {POST_FILTERS.map(([v, l]) => (
            <button key={l} className={`chip${status === v ? " chip-on" : ""}`} onClick={() => setStatus(v)}>{l}</button>
          ))}
          <select value={channel} onChange={(e) => setChannel(e.target.value)}>
            <option value="">все каналы</option>
            {channels.map((c) => <option key={c.key} value={c.key}>@{c.username}</option>)}
          </select>
        </div>
      }
    >
      <ErrorNote error={error ?? del.error} />
      {data && data.length === 0 && <Empty>Пусто</Empty>}
      <div className="posts">
        {data?.map((p) => (
          <article key={p.id} className="post">
            <header>
              <span className="badge">{STATUS_LABEL[p.status] ?? p.status}</span>
              <span>@{user[p.channel] ?? p.channel}</span>
              <span className="muted">скор {p.score}{p.urgent ? " · СРОЧНО" : ""} · {time(p.published_at || p.created)}</span>
              {["published", "previewed", "ready", "media", "draft"].includes(p.status) && (
                <button className="btn btn-danger" disabled={del.isPending}
                  onClick={() => confirm(p.status === "published" ? "Удалить пост из канала?" : "Снять пост?") && del.mutate(p.id)}>
                  {p.status === "published" ? "Удалить" : "Снять"}
                </button>
              )}
            </header>
            {p.media_count > 0 && (
              <div className="post-media">
                {Array.from({ length: p.media_count }, (_, i) => (
                  <a key={i} href={`/api/channels/posts/${p.id}/media/${i}`} target="_blank" rel="noreferrer">медиа {i + 1}</a>
                ))}
              </div>
            )}
            {p.html ? <div className="post-text" dangerouslySetInnerHTML={{ __html: safeHtml(p.html) }} /> : <div className="muted">{p.title}</div>}
            {p.error && <div className="detail-failed">{p.error}</div>}
            <div className="muted small">медиа: {p.media_note || "—"}</div>
            {p.sources?.length > 0 && (
              <details><summary className="muted small">источники ({p.sources.length})</summary>
                {p.sources.map((s) => <div key={s}><a href={s} target="_blank" rel="noreferrer">{s}</a></div>)}
              </details>
            )}
          </article>
        ))}
      </div>
    </Card>
  );
}

// ---- stories ----------------------------------------------------------------
function StoriesTab() {
  const [status, setStatus] = useState("");
  const { data, error } = useQuery({
    queryKey: ["ch-stories", status],
    queryFn: () => api.get<Story[]>(`/channels/stories?limit=150&status=${status}`),
    refetchInterval: 30000,
  });
  return (
    <Card
      title="Сюжеты и оценки ИИ"
      actions={
        <div className="chips">
          {[["", "Все"], ["selected", "Взяты"], ["judged", "Ниже порога"], ["rejected", "Отсеяны"]].map(([v, l]) => (
            <button key={l} className={`chip${status === v ? " chip-on" : ""}`} onClick={() => setStatus(v)}>{l}</button>
          ))}
        </div>
      }
    >
      <ErrorNote error={error} />
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Скор</th><th>Сюжет</th><th className="hide-mobile">Регион</th><th>Каналов</th><th className="hide-mobile">Почему</th></tr></thead>
          <tbody>
            {data?.map((s) => (
              <tr key={s.id}>
                <td className="nowrap">{s.score?.toFixed(1)} <span className="muted small">{s.status}</span></td>
                <td>{s.title || s.lead.slice(0, 140)} <span className="muted small">{time(s.first_ts)} · {s.category}</span></td>
                <td className="hide-mobile">{s.region}</td>
                <td>{s.n_channels}</td>
                <td className="hide-mobile muted small">{s.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

// ---- sources ----------------------------------------------------------------
function SourcesTab() {
  const qc = useQueryClient();
  const { data, error } = useQuery({ queryKey: ["ch-sources"], queryFn: () => api.get<Source[]>("/channels/sources") });
  const [form, setForm] = useState({ platform: "telegram", ref: "", title: "", region: "russia", kind: "aggregator", trust: 0.6, every_min: 10 });
  const [q, setQ] = useState("");
  const inv = () => qc.invalidateQueries({ queryKey: ["ch-sources"] });
  const add = useMutation({ mutationFn: () => api.post("/channels/sources", form), onSuccess: () => { inv(); setForm({ ...form, ref: "", title: "" }); } });
  const patch = useMutation({ mutationFn: ({ id, body }: { id: number; body: Record<string, unknown> }) => api.patch(`/channels/sources/${id}`, body), onSuccess: inv });
  const del = useMutation({ mutationFn: (id: number) => api.del(`/channels/sources/${id}`), onSuccess: inv });
  const rows = (data ?? []).filter((s) => !q || `${s.ref} ${s.title}`.toLowerCase().includes(q.toLowerCase()));
  return (
    <>
      <Card title="Добавить источник">
        <form className="form ch-form" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
          <label>Платформа
            <select value={form.platform} onChange={(e) => setForm({ ...form, platform: e.target.value })}>
              {["telegram", "rss", "site", "rutube", "youtube"].map((p) => <option key={p}>{p}</option>)}
            </select>
          </label>
          <label className="span-2">Канал / адрес<input value={form.ref} placeholder="@channel или https://…/rss" onChange={(e) => setForm({ ...form, ref: e.target.value })} /></label>
          <label>Название<input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></label>
          <label>Регион
            <select value={form.region} onChange={(e) => setForm({ ...form, region: e.target.value })}>
              {Object.entries(REGION_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
          <label>Тип
            <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
              {Object.entries(KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
          <label>Доверие 0–1<input type="number" step="0.05" min="0" max="1" value={form.trust} onChange={(e) => setForm({ ...form, trust: Number(e.target.value) })} /></label>
          <label>Читать раз в, мин<input type="number" min="1" value={form.every_min} onChange={(e) => setForm({ ...form, every_min: Number(e.target.value) })} /></label>
          <div className="form-foot">
            <ErrorNote error={add.error} />
            <button className="btn btn-primary" disabled={!form.ref.trim() || add.isPending}>Добавить</button>
          </div>
        </form>
      </Card>
      <Card title={`Источники (${data?.length ?? 0})`} actions={<input placeholder="поиск" value={q} onChange={(e) => setQ(e.target.value)} />}>
        <ErrorNote error={error ?? patch.error ?? del.error} />
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Источник</th><th className="hide-mobile">Тип</th><th className="hide-mobile">Регион</th><th>Доверие</th><th className="hide-mobile">Прочитан</th><th /></tr></thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.id} className={s.enabled ? "" : "muted"}>
                  <td>
                    {s.platform === "telegram" ? <a href={`https://t.me/${s.ref}`} target="_blank" rel="noreferrer">@{s.ref}</a> : <span className="clamp">{s.ref}</span>}
                    <div className="muted small">{s.title} · {s.platform}{s.joined ? " · подписан" : ""} · постов {s.posts_seen}</div>
                    {(s.error || s.note) && <div className="detail-failed small">{s.error || s.note}</div>}
                  </td>
                  <td className="hide-mobile">{KIND_LABEL[s.kind] ?? s.kind}</td>
                  <td className="hide-mobile">{REGION_LABEL[s.region] ?? s.region}</td>
                  <td>
                    <input className="trust" type="number" step="0.05" min="0" max="1" defaultValue={s.trust}
                      onBlur={(e) => Number(e.target.value) !== s.trust && patch.mutate({ id: s.id, body: { trust: Number(e.target.value) } })} />
                  </td>
                  <td className="hide-mobile nowrap">{time(s.last_read)}</td>
                  <td className="nowrap">
                    <button className="btn btn-ghost" onClick={() => patch.mutate({ id: s.id, body: { enabled: !s.enabled } })}>{s.enabled ? "выкл" : "вкл"}</button>
                    <button className="btn btn-danger" onClick={() => confirm(`Удалить источник ${s.ref}?`) && del.mutate(s.id)}>×</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}

// ---- style and AI -----------------------------------------------------------
type Settings = Record<string, string | number | boolean>;
const TEXT_KEYS: [string, string][] = [
  ["style_common", "Общие правила (честность, без источника, формат)"],
  ["style_news", "Стайлгайд основного канала"],
  ["style_city", "Стайлгайд городских каналов ({title}, {city_gen} подставляются)"],
];
const FIELD_KEYS: [string, string][] = [
  ["model_judge", "Модель: оценка сюжетов"], ["model_write", "Модель: переписывание"], ["model_check", "Модель: сверка фактов"],
  ["model_vision", "Модель: поиск знаков на медиа"], ["model_vision2", "Вторая модель-зритель"],
  ["daily_budget_usd", "Бюджет ИИ в день, $"], ["video_max_s", "Видео не длиннее, с"],
  ["inpaint_video_max_s", "Чистить сервисом видео до, с"], ["crop_max_share", "Обрезать знак у края, доля кадра"],
  ["max_media", "Медиа в посте, до"], ["join_every_s", "Вступать в канал раз в, с"], ["max_channels", "Каналов у аккаунта, до"],
  ["primary_bonus", "Бонус первоисточнику (к оценке)"], ["fresh_burst", "Свежему сильному — постов сверх графика"],
  ["fresh_min", "«Свежий» — первые, мин"],
];

function StyleTab() {
  const qc = useQueryClient();
  const { data, error } = useQuery({ queryKey: ["ch-settings"], queryFn: () => api.get<Settings>("/channels/settings") });
  const [form, setForm] = useState<Settings>({});
  useEffect(() => { if (data) setForm(data); }, [data]);
  const save = useMutation({
    mutationFn: () => api.put<Settings>("/channels/settings", { values: form }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ch-settings"] }),
  });
  if (!data) return <ErrorNote error={error} />;
  const numeric = (k: string) => typeof data[k] === "number";
  return (
    <Card title="Стайлгайды, промпты и модели">
      <form className="form" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        {TEXT_KEYS.map(([k, l]) => (
          <label key={k}>{l}
            <textarea rows={k === "style_common" ? 18 : 10} value={String(form[k] ?? "")} onChange={(e) => setForm({ ...form, [k]: e.target.value })} />
          </label>
        ))}
        <div className="ch-form">
          {FIELD_KEYS.map(([k, l]) => (
            <label key={k}>{l}
              <input value={String(form[k] ?? "")} type={numeric(k) ? "number" : "text"} step="any"
                onChange={(e) => setForm({ ...form, [k]: numeric(k) ? Number(e.target.value) : e.target.value })} />
            </label>
          ))}
          <label>Вступать в каналы-источники
            <select value={form.join_enabled ? "1" : "0"} onChange={(e) => setForm({ ...form, join_enabled: e.target.value === "1" })}>
              <option value="1">да</option><option value="0">нет</option>
            </select>
          </label>
        </div>
        <div className="form-foot">
          <ErrorNote error={save.error} />
          <button className="btn btn-primary" disabled={save.isPending}>{save.isPending ? "Сохраняю…" : "Сохранить"}</button>
        </div>
      </form>
    </Card>
  );
}

// ---- page -------------------------------------------------------------------
const TABS: [string, string][] = [["overview", "Каналы"], ["posts", "Очередь и история"], ["stories", "Сюжеты"], ["sources", "Источники"], ["style", "Стиль и ИИ"]];

export function ChannelsPage() {
  const [tab, setTab] = useState("overview");
  const { data, error } = useQuery({
    queryKey: ["ch-overview"],
    queryFn: () => api.get<Overview>("/channels/overview"),
    refetchInterval: 20000,
  });
  return (
    <div className="page">
      <header className="page-head">
        <h1>Каналы ЗАСНЯЛИ</h1>
        <div className="chips">
          {TABS.map(([k, l]) => (
            <button key={k} className={`chip${tab === k ? " chip-on" : ""}`} onClick={() => setTab(k)}>{l}</button>
          ))}
        </div>
      </header>
      <ErrorNote error={error} />
      {tab === "overview" && data && <OverviewTab data={data} />}
      {tab === "posts" && <PostsTab channels={data?.channels ?? []} />}
      {tab === "stories" && <StoriesTab />}
      {tab === "sources" && <SourcesTab />}
      {tab === "style" && <StyleTab />}
    </div>
  );
}
