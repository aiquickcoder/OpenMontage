import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { Card, ErrorNote } from "../components/ui";
import { PLACEHOLDER_LABEL, PLACEHOLDERS, PLACEMENT_LABEL, unixDate, unknownPlaceholders } from "../ads";
import type { Advertiser, Campaign, Insert, InsertPreview, InsertVersion, Placement } from "../types/ads";
import "../ads.css";

interface Draft {
  name: string;
  placement: Placement;
  share: number; // percent 0..50
  template: string;
}

const toDraft = (i: Pick<Insert, "name" | "placement" | "max_screen_share" | "template">): Draft => ({
  name: i.name,
  placement: i.placement,
  share: Math.round(i.max_screen_share * 100),
  template: i.template,
});

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function Preview({ insertId, template }: { insertId: string; template: string }) {
  const advertisers = useQuery({ queryKey: ["advertisers", false], queryFn: () => api.get<Advertiser[]>("/advertisers") });
  const campaigns = useQuery({ queryKey: ["campaigns"], queryFn: () => api.get<Campaign[]>("/campaigns") });
  const [advId, setAdvId] = useState("");
  const [campId, setCampId] = useState("");
  useEffect(() => {
    if (!advId && advertisers.data?.length) setAdvId(advertisers.data[0].id);
  }, [advId, advertisers.data]);
  const debounced = useDebounced(template, 400);
  const bad = unknownPlaceholders(debounced).length > 0;
  const preview = useQuery({
    queryKey: ["insert-preview", insertId, advId, campId, debounced],
    queryFn: () =>
      api.post<InsertPreview>(`/inserts/${encodeURIComponent(insertId)}/preview`, {
        advertiser_id: advId,
        campaign_id: campId || null,
        template: debounced,
      }),
    enabled: !!advId && !bad,
    retry: false,
  });
  const camps = (campaigns.data ?? []).filter((c) => c.advertiser_id === advId);

  return (
    <Card title="Предпросмотр">
      <div className="form">
        <label>
          Рекламодатель
          <select value={advId} onChange={(e) => { setAdvId(e.target.value); setCampId(""); }}>
            {(advertisers.data ?? []).map((a) => (
              <option key={a.id} value={a.id}>
                {a.brand}
              </option>
            ))}
          </select>
        </label>
        <label>
          Кампания (продукт, ERID)
          <select value={campId} onChange={(e) => setCampId(e.target.value)}>
            <option value="">— без кампании —</option>
            {camps.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      {advertisers.data?.length === 0 && <p className="muted">Добавьте рекламодателя, чтобы увидеть подстановку.</p>}
      {bad ? (
        <p className="ads-bad">Исправьте неизвестные подстановки — предпросмотр недоступен.</p>
      ) : (
        <>
          <div className="ads-preview">{preview.data?.text ?? (preview.isFetching ? "…" : "")}</div>
          {preview.data?.marking && <div className="ads-marking">{preview.data.marking}</div>}
        </>
      )}
      <ErrorNote error={preview.error} />
    </Card>
  );
}

export function InsertPage() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const area = useRef<HTMLTextAreaElement>(null);
  const { data: ins, error } = useQuery({
    queryKey: ["insert", id],
    queryFn: () => api.get<Insert>(`/inserts/${encodeURIComponent(id)}`),
  });
  const versions = useQuery({
    queryKey: ["insert-versions", id],
    queryFn: () => api.get<InsertVersion[]>(`/inserts/${encodeURIComponent(id)}/versions`),
  });
  const [draft, setDraft] = useState<Draft | null>(null);
  useEffect(() => {
    if (ins) setDraft(toDraft(ins));
  }, [ins]);

  const refresh = (fresh: Insert) => {
    qc.setQueryData(["insert", id], fresh);
    qc.invalidateQueries({ queryKey: ["inserts"] });
    qc.invalidateQueries({ queryKey: ["insert-versions", id] });
  };
  const save = useMutation({
    mutationFn: (d: Draft) =>
      api.patch<Insert>(`/inserts/${encodeURIComponent(id)}`, {
        name: d.name,
        placement: d.placement,
        max_screen_share: d.share / 100,
        template: d.template,
      }),
    onSuccess: refresh,
  });
  const archive = useMutation({
    mutationFn: (archived: boolean) => api.patch<Insert>(`/inserts/${encodeURIComponent(id)}`, { archived }),
    onSuccess: refresh,
  });

  if (error) return <div className="page"><ErrorNote error={error} /></div>;
  if (!ins || !draft) return <div className="page muted">Загрузка…</div>;

  const unknown = unknownPlaceholders(draft.template);
  const dirty = JSON.stringify(draft) !== JSON.stringify(toDraft(ins));
  const insertChip = (name: string) => {
    const el = area.current;
    const token = `{${name}}`;
    const start = el?.selectionStart ?? draft.template.length;
    const end = el?.selectionEnd ?? start;
    const template = draft.template.slice(0, start) + token + draft.template.slice(end);
    setDraft({ ...draft, template });
    requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(start + token.length, start + token.length);
    });
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate(draft);
  };

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <Link to="/inserts" className="muted">← Шаблоны вставок</Link>
          <h1>{ins.name}</h1>
          <span className="muted">
            версия {ins.version}
            {ins.archived ? " · в архиве" : ""}
          </span>
        </div>
        <button className="btn btn-ghost" disabled={archive.isPending} onClick={() => archive.mutate(!ins.archived)}>
          {ins.archived ? "Вернуть из архива" : "В архив"}
        </button>
      </header>

      <div className="ads-two">
        <Card title="Шаблон">
          <form className="form" onSubmit={submit}>
            <label className="span-2">
              Название
              <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            </label>
            <label>
              Размещение
              <select value={draft.placement} onChange={(e) => setDraft({ ...draft, placement: e.target.value as Placement })}>
                {Object.entries(PLACEMENT_LABEL).map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Макс. доля экранного времени: {draft.share}%
              <input
                type="range"
                min={0}
                max={50}
                step={5}
                value={draft.share}
                onChange={(e) => setDraft({ ...draft, share: Number(e.target.value) })}
              />
            </label>
            <div className="span-2 chips">
              {PLACEHOLDERS.map((p) => (
                <button key={p} type="button" className="chip" title={PLACEHOLDER_LABEL[p]} onClick={() => insertChip(p)}>
                  {`{${p}}`}
                </button>
              ))}
            </div>
            <label className="span-2">
              Текст для агента
              <textarea
                ref={area}
                className="ads-template"
                value={draft.template}
                onChange={(e) => setDraft({ ...draft, template: e.target.value })}
              />
            </label>
            {unknown.length > 0 && (
              <p className="span-2 ads-bad">
                Неизвестные подстановки: {unknown.map((u) => `{${u}}`).join(", ")}. Фигурные скобки как текст — {"{{ и }}"}.
              </p>
            )}
            <div className="span-2 form-foot">
              <ErrorNote error={save.error || archive.error} />
              <button className="btn btn-primary" disabled={!dirty || unknown.length > 0 || !draft.name.trim() || save.isPending}>
                {save.isPending ? "Сохраняю…" : "Сохранить новую версию"}
              </button>
            </div>
          </form>
          <p className="muted">Кампании закрепляют версию: правка шаблона не меняет уже идущие кампании.</p>
        </Card>

        <div>
          <Preview insertId={ins.id} template={draft.template} />
          <Card title="История версий">
            <ul className="ads-versions">
              {(versions.data ?? []).map((v) => (
                <li key={v.version}>
                  <span>
                    <strong>v{v.version}</strong> <span className="muted">{unixDate(v.created_at)}</span>
                    <div className="muted clamp">{v.template}</div>
                  </span>
                  <button className="btn btn-ghost" onClick={() => setDraft(toDraft(v))} title="Загрузить в редактор">
                    Открыть
                  </button>
                </li>
              ))}
            </ul>
            <ErrorNote error={versions.error} />
          </Card>
        </div>
      </div>
    </div>
  );
}
