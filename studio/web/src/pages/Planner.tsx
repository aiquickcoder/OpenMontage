import { useMemo, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { Card, ErrorNote } from "../components/ui";
import { PlanWeekForm } from "../components/WorldTasks";
import { shortId } from "../format";
import type { Character } from "../types/characters";
import {
  addDays,
  dayLabel,
  EDITABLE_EPISODE,
  EPISODE_STATUS_LABEL,
  mondayOf,
  parseDay,
  today,
  type Episode,
  type EpisodeStatus,
  type World,
} from "../types/worlds";
import "../worlds.css";

function StatusBadge({ status }: { status: EpisodeStatus }) {
  return <span className={`badge ep-status-${status}`}>{EPISODE_STATUS_LABEL[status] ?? status}</span>;
}

type Editing = { ep: Episode } | { create: { world_id: string; air_date: string } };

function EpisodeDrawer({ editing, worldName, onClose }: { editing: Editing; worldName: (id: string) => string; onClose: () => void }) {
  const qc = useQueryClient();
  const ep = "ep" in editing ? editing.ep : null;
  const worldId = ep ? ep.world_id : "create" in editing ? editing.create.world_id : "";
  const [form, setForm] = useState(() => ({
    title: ep?.title ?? "",
    logline: ep?.logline ?? "",
    synopsis: ep?.synopsis ?? "",
    air_date: ep?.air_date ?? ("create" in editing ? editing.create.air_date : today()),
    notes: ep?.notes ?? "",
    characters: ep?.characters ?? ([] as string[]),
  }));
  const chars = useQuery({
    queryKey: ["characters", "world", worldId],
    queryFn: () => api.get<Character[]>(`/characters?world=${encodeURIComponent(worldId)}`),
    enabled: !!worldId,
  });
  const done = () => {
    qc.invalidateQueries({ queryKey: ["episodes"] });
    onClose();
  };
  const body = () => ({
    title: form.title.trim(),
    logline: form.logline.trim(),
    synopsis: form.synopsis.trim(),
    air_date: form.air_date,
    notes: form.notes.trim(),
    characters: form.characters,
  });
  const save = useMutation({
    mutationFn: (status?: "draft" | "approved") =>
      ep
        ? api.patch<Episode>(`/episodes/${encodeURIComponent(ep.id)}`, { ...body(), ...(status ? { status } : {}) })
        : api.post<Episode>("/episodes", { ...body(), world_id: worldId }).then((created) =>
            status === "approved" ? api.patch<Episode>(`/episodes/${encodeURIComponent(created.id)}`, { status }) : created,
          ),
    onSuccess: done,
  });
  const remove = useMutation({
    mutationFn: () => api.del<{ ok: boolean }>(`/episodes/${encodeURIComponent(ep!.id)}`),
    onSuccess: done,
  });
  const editable = !ep || EDITABLE_EPISODE.includes(ep.status);
  const busy = save.isPending || remove.isPending;
  const valid = form.title.trim().length > 0 && !!form.air_date;
  const toggle = (id: string) =>
    setForm({ ...form, characters: form.characters.includes(id) ? form.characters.filter((c) => c !== id) : [...form.characters, id] });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate(undefined);
  };
  const charName = (id: string) => chars.data?.find((c) => c.id === id)?.name ?? id;

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer ep-drawer" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Эпизод">
        <header className="drawer-head">
          <div>
            <h2>{ep ? ep.title : "Новый эпизод"}</h2>
            <span className="muted">
              {worldName(worldId)}
              {ep && ` · ${ep.source === "ai" ? "ИИ" : "вручную"}`}
            </span>
            {ep && (
              <div style={{ marginTop: 4 }}>
                <StatusBadge status={ep.status} />
              </div>
            )}
          </div>
          <button className="btn btn-ghost" onClick={onClose} aria-label="Закрыть">
            ✕
          </button>
        </header>
        <div className="drawer-body">
          {!editable && ep ? (
            <>
              <dl className="ep-readonly" style={{ margin: 0 }}>
                <dt>Дата выхода</dt>
                <dd>{dayLabel(ep.air_date)}</dd>
                {ep.logline && (
                  <>
                    <dt>Логлайн</dt>
                    <dd>{ep.logline}</dd>
                  </>
                )}
                {ep.synopsis && (
                  <>
                    <dt>Синопсис</dt>
                    <dd>{ep.synopsis}</dd>
                  </>
                )}
                {ep.characters.length > 0 && (
                  <>
                    <dt>Персонажи</dt>
                    <dd>{ep.characters.map(charName).join(", ")}</dd>
                  </>
                )}
                {ep.notes && (
                  <>
                    <dt>Заметки</dt>
                    <dd>{ep.notes}</dd>
                  </>
                )}
              </dl>
              <div className="muted">Эпизод ушёл в производство — редактировать нельзя.</div>
              {ep.brief_id && (
                <div className="drawer-actions">
                  <Link className="btn btn-primary" to={`/briefs/${ep.brief_id}`}>
                    Бриф {shortId(ep.brief_id)}
                  </Link>
                </div>
              )}
            </>
          ) : (
            <form className="form" onSubmit={submit}>
              <label>
                Название
                <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} maxLength={200} />
              </label>
              <label>
                Дата выхода
                <input type="date" value={form.air_date} onChange={(e) => setForm({ ...form, air_date: e.target.value })} />
              </label>
              <label>
                Логлайн
                <textarea rows={2} value={form.logline} onChange={(e) => setForm({ ...form, logline: e.target.value })} maxLength={1000} />
              </label>
              <label>
                Синопсис
                <textarea rows={6} value={form.synopsis} onChange={(e) => setForm({ ...form, synopsis: e.target.value })} maxLength={4000} />
              </label>
              <div>
                <span className="field-label">Персонажи</span>
                {chars.error ? (
                  <ErrorNote error={chars.error} />
                ) : !chars.data ? (
                  <div className="muted">Загрузка…</div>
                ) : chars.data.length === 0 ? (
                  <div className="muted">У мира нет персонажей</div>
                ) : (
                  <div className="ep-chars">
                    {chars.data.map((c) => (
                      <label key={c.id} className="draft-check">
                        <input type="checkbox" checked={form.characters.includes(c.id)} onChange={() => toggle(c.id)} />
                        {c.name}
                      </label>
                    ))}
                  </div>
                )}
              </div>
              <label>
                Заметки
                <textarea rows={3} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} maxLength={2000} />
              </label>
              <ErrorNote error={save.error || remove.error} />
              <div className="drawer-actions">
                <button className="btn btn-primary" disabled={!valid || busy}>
                  {save.isPending && save.variables === undefined ? "Сохраняю…" : "Сохранить"}
                </button>
                {(!ep || ep.status === "draft") && (
                  <button type="button" className="btn" disabled={!valid || busy} onClick={() => save.mutate("approved")}>
                    Утвердить
                  </button>
                )}
                {ep?.status === "approved" && (
                  <button type="button" className="btn" disabled={!valid || busy} onClick={() => save.mutate("draft")}>
                    Вернуть в черновик
                  </button>
                )}
                {ep && (
                  <button
                    type="button"
                    className="btn btn-danger"
                    disabled={busy}
                    onClick={() => window.confirm("Удалить эпизод?") && remove.mutate()}
                  >
                    Удалить
                  </button>
                )}
              </div>
            </form>
          )}
        </div>
      </aside>
    </div>
  );
}

function EpisodeCard({ ep, worldName, showWorld, onOpen }: { ep: Episode; worldName: string; showWorld: boolean; onOpen: () => void }) {
  return (
    <button type="button" className={`ep-card ep-card-${ep.status}`} onClick={onOpen}>
      {showWorld && <span className="ep-card-world">{worldName}</span>}
      <span className="ep-card-title">{ep.title}</span>
      {ep.logline && <span className="ep-card-log clamp-3">{ep.logline}</span>}
      <span className="ep-card-meta">
        <StatusBadge status={ep.status} />
        <span className="ep-source">{ep.source === "ai" ? "ИИ" : "вручную"}</span>
      </span>
    </button>
  );
}

export function PlannerPage() {
  const [params, setParams] = useSearchParams();
  const worldId = params.get("world") ?? "";
  const week = mondayOf(params.get("week") || today());
  const end = addDays(week, 6);
  const days = useMemo(() => Array.from({ length: 7 }, (_, i) => addDays(week, i)), [week]);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [planning, setPlanning] = useState(false);
  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    setParams(next, { replace: true });
  };

  const worlds = useQuery({ queryKey: ["worlds", "0"], queryFn: () => api.get<World[]>("/worlds?archived=0") });
  const names = useMemo(() => new Map((worlds.data ?? []).map((w) => [w.id, w.name])), [worlds.data]);
  const worldName = (id: string) => names.get(id) ?? id;
  const { data, error } = useQuery({
    queryKey: ["episodes", week, end, worldId],
    queryFn: () => api.get<Episode[]>(`/episodes?${new URLSearchParams({ start: week, end, world: worldId }).toString()}`),
    refetchInterval: 30_000,
  });
  const byDay = useMemo(() => {
    const m = new Map<string, Episode[]>();
    for (const ep of data ?? []) m.set(ep.air_date, [...(m.get(ep.air_date) ?? []), ep]);
    return m;
  }, [data]);
  const now = today();
  const range = `${parseDay(week).toLocaleDateString("ru-RU", { day: "numeric", month: "short" })} — ${parseDay(end).toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "short",
    year: "numeric",
  })}`;

  return (
    <div className="page">
      <header className="page-head">
        <h1>План</h1>
        <div className="plan-toolbar">
          <select value={worldId} onChange={(e) => set({ world: e.target.value })} aria-label="Мир">
            <option value="">Все миры</option>
            {(worlds.data ?? []).map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
            {worldId && !names.has(worldId) && <option value={worldId}>{worldId}</option>}
          </select>
          <button className="btn" disabled={!worldId} onClick={() => setPlanning(!planning)} title={worldId ? "" : "Выберите мир"}>
            Спланировать неделю (ИИ)
          </button>
        </div>
      </header>

      <div className="plan-hint">Утверждённые эпизоды уходят в производство в день выхода (1 в день, с 06:00 UTC).</div>

      {planning && worldId && (
        <Card
          title={`Спланировать неделю — ${worldName(worldId)}`}
          actions={
            <button className="btn btn-ghost" onClick={() => setPlanning(false)} aria-label="Закрыть">
              ✕
            </button>
          }
        >
          <PlanWeekForm key={worldId} worldId={worldId} plannerLink={false} />
        </Card>
      )}

      <div className="plan-toolbar">
        <button className="btn" onClick={() => set({ week: addDays(week, -7) })} aria-label="Предыдущая неделя">
          ←
        </button>
        <button className="btn" onClick={() => set({ week: "" })} disabled={week === mondayOf(now)}>
          Сегодня
        </button>
        <button className="btn" onClick={() => set({ week: addDays(week, 7) })} aria-label="Следующая неделя">
          →
        </button>
        <span className="plan-week-title">{range}</span>
      </div>

      <ErrorNote error={error || worlds.error} />
      <div className="plan-grid">
        {days.map((day) => {
          const eps = byDay.get(day) ?? [];
          return (
            <section key={day} className={`plan-day${day === now ? " plan-day-today" : ""}${day < now ? " plan-day-past" : ""}`}>
              <div className="plan-day-head">
                <span>{dayLabel(day)}</span>
                {day === now && <span>сегодня</span>}
              </div>
              {!data && !error && <span className="muted">…</span>}
              {eps.map((ep) => (
                <EpisodeCard key={ep.id} ep={ep} worldName={worldName(ep.world_id)} showWorld={!worldId} onOpen={() => setEditing({ ep })} />
              ))}
              <button
                type="button"
                className="plan-add"
                disabled={!worldId}
                title={worldId ? "" : "Выберите мир, чтобы добавить эпизод"}
                onClick={() => setEditing({ create: { world_id: worldId, air_date: day } })}
              >
                + Эпизод
              </button>
            </section>
          );
        })}
      </div>
      {!worldId && <div className="muted">Чтобы добавить эпизод или спланировать неделю, выберите мир.</div>}

      {editing && (
        <EpisodeDrawer
          key={"ep" in editing ? editing.ep.id : editing.create.air_date}
          editing={editing}
          worldName={worldName}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}
