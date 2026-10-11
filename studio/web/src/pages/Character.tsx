import { useEffect, useState, type ChangeEvent, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { Card, ErrorNote } from "../components/ui";
import { CharacterGeneration } from "../components/CharacterGeneration";
import { dateTime } from "../format";
import {
  charFileUrl,
  ROLE_LABEL,
  uploadCharacterImage,
  type Character,
  type CharacterRole,
  type ImageKind,
} from "../types/characters";
import "../characters.css";

const ACCEPT = "image/png,image/jpeg,image/webp";
const MAX_BYTES = 15 * 1024 * 1024;

function sourceLabel(source: string) {
  if (source === "manual") return "создан вручную";
  if (source === "generated") return "нарисован в Flow";
  if (source.startsWith("project:")) return `из проекта ${source.slice(8)}`;
  return source;
}

function UploadButton({ id, kind, label }: { id: string; kind: ImageKind; label: string }) {
  const qc = useQueryClient();
  const up = useMutation({
    mutationFn: (file: File) => {
      if (file.size > MAX_BYTES) throw new Error("Файл больше 15 МБ");
      return uploadCharacterImage(id, kind, file);
    },
    onSuccess: (r) => {
      qc.setQueryData(["character", id], r.character);
      qc.invalidateQueries({ queryKey: ["characters"] });
    },
  });
  const pick = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (file) up.mutate(file);
  };
  return (
    <>
      <label className="btn char-upload">
        {up.isPending ? "Загружаю…" : label}
        <input type="file" accept={ACCEPT} onChange={pick} disabled={up.isPending} />
      </label>
      <ErrorNote error={up.error} />
    </>
  );
}

function Sheet({ c, rel, title, kind, onZoom }: { c: Character; rel?: string; title: string; kind: ImageKind; onZoom: (rel: string) => void }) {
  return (
    <div className="char-sheet">
      <div className="muted">{title}</div>
      <div className="char-sheet-img">
        {rel ? (
          <img src={charFileUrl(c.id, rel, 960)} alt={title} onClick={() => onZoom(rel)} style={{ cursor: "zoom-in" }} />
        ) : (
          <span>Нет картинки</span>
        )}
      </div>
      <div className="char-actions">
        <UploadButton id={c.id} kind={kind} label={rel ? "Заменить" : "Загрузить"} />
      </div>
    </div>
  );
}

function EditForm({ c }: { c: Character }) {
  const qc = useQueryClient();
  const [form, setForm] = useState(() => toForm(c));
  useEffect(() => setForm(toForm(c)), [c]);
  const save = useMutation({
    mutationFn: () =>
      api.patch<Character>(`/characters/${encodeURIComponent(c.id)}`, {
        name: form.name.trim(),
        role: form.role,
        description: form.description.trim(),
        prompt_tokens: form.prompt_tokens.trim(),
        voice: { language: form.language.trim() || "ru", description: form.voice.trim() },
        style_lock: form.style_lock.trim(),
        tags: form.tags.split(",").map((t) => t.trim()).filter(Boolean),
      }),
    onSuccess: (r) => {
      qc.setQueryData(["character", c.id], r);
      qc.invalidateQueries({ queryKey: ["characters"] });
    },
  });
  const field = (key: keyof ReturnType<typeof toForm>) => ({
    value: form[key],
    onChange: (e: ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => setForm({ ...form, [key]: e.target.value }),
  });
  const dirty = JSON.stringify(form) !== JSON.stringify(toForm(c));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };

  return (
    <form className="form" onSubmit={submit}>
      <label>
        Имя
        <input {...field("name")} maxLength={80} />
      </label>
      <label>
        Роль
        <select {...field("role")}>
          {!(form.role in ROLE_LABEL) && <option value={form.role}>{form.role}</option>}
          {(Object.keys(ROLE_LABEL) as CharacterRole[]).map((r) => (
            <option key={r} value={r}>
              {ROLE_LABEL[r]}
            </option>
          ))}
        </select>
      </label>
      <label className="span-2">
        Внешность (description)
        <textarea rows={4} {...field("description")} maxLength={2000} />
      </label>
      <label className="span-2">
        Токены промпта — вставляются в каждый кадр дословно
        <textarea rows={2} {...field("prompt_tokens")} maxLength={400} />
      </label>
      <label>
        Голос — дословно в каждой реплике
        <input {...field("voice")} maxLength={600} />
      </label>
      <label>
        Язык голоса
        <input {...field("language")} maxLength={20} />
      </label>
      <label className="span-2">
        Стиль (style lock)
        <textarea rows={2} {...field("style_lock")} maxLength={1000} />
      </label>
      <label className="span-2">
        Теги через запятую
        <input {...field("tags")} placeholder="кот, повар, пицца" />
      </label>
      <div className="span-2 form-foot">
        <ErrorNote error={save.error} />
        {save.isSuccess && !dirty && <span className="muted">Сохранено</span>}
        <button className="btn btn-primary" disabled={!dirty || save.isPending || !form.name.trim()}>
          {save.isPending ? "Сохраняю…" : "Сохранить"}
        </button>
      </div>
    </form>
  );
}

function toForm(c: Character) {
  return {
    name: c.name,
    role: c.role,
    description: c.description,
    prompt_tokens: c.prompt_tokens,
    voice: c.voice?.description ?? "",
    language: c.voice?.language ?? "ru",
    style_lock: c.library.style_lock ?? "",
    tags: (c.library.tags ?? []).join(", "),
  };
}

export function CharacterPage() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const [zoom, setZoom] = useState<string | null>(null);
  const { data: c, error } = useQuery({
    queryKey: ["character", id],
    queryFn: () => api.get<Character>(`/characters/${encodeURIComponent(id)}`),
  });
  const archive = useMutation({
    mutationFn: (archived: boolean): Promise<unknown> =>
      archived
        ? api.del<{ ok: boolean }>(`/characters/${encodeURIComponent(id)}`)
        : api.patch<Character>(`/characters/${encodeURIComponent(id)}`, { archived: false }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["character", id] });
      qc.invalidateQueries({ queryKey: ["characters"] });
    },
  });

  if (error) return <ErrorNote error={error} />;
  if (!c) return <div className="loading">Загрузка…</div>;
  const variants = c.files?.variants ?? [];
  const refs = c.files?.refs ?? [];
  const taskId = c.library.task_id;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <Link to="/characters" className="muted">
            ← Персонажи
          </Link>
          <h1>{c.name}</h1>
          <div className="muted">
            {ROLE_LABEL[c.role as CharacterRole] ?? c.role} · {sourceLabel(c.library.source)} · обновлён {dateTime(c.library.updated_at)}
            {c.library.archived && " · в архиве"}
          </div>
        </div>
        <div className="char-actions">
          {!c.library.archived && c.turnaround_path && (
            <Link className="btn btn-primary" to={`/briefs?characters=${encodeURIComponent(c.id)}`}>
              Использовать в брифе
            </Link>
          )}
          <button
            className={c.library.archived ? "btn" : "btn btn-danger"}
            disabled={archive.isPending}
            onClick={() => archive.mutate(!c.library.archived)}
          >
            {c.library.archived ? "Вернуть из архива" : "В архив"}
          </button>
        </div>
      </header>
      <ErrorNote error={archive.error} />

      {taskId && (c.task?.status !== "done" || !c.turnaround_path) && (
        <Card title="Генерация листа">
          <CharacterGeneration characterId={c.id} taskId={taskId} />
        </Card>
      )}

      <Card title="Листы персонажа">
        <div className="char-sheets">
          <Sheet c={c} rel={c.turnaround_path || undefined} title="Разворот (front / ¾ / профиль)" kind="turnaround" onZoom={setZoom} />
          <Sheet c={c} rel={c.expression_sheet_path} title="Эмоции" kind="expression" onZoom={setZoom} />
        </div>
        {c.flow_media_ids && c.flow_media_ids.length > 0 && (
          <p className="muted">Flow media id: {c.flow_media_ids.join(", ")} (может не сработать на другом аккаунте)</p>
        )}
      </Card>

      <Card title={`Референсы (${refs.length})`} actions={<UploadButton id={c.id} kind="ref" label="Добавить" />}>
        {refs.length === 0 ? (
          <div className="muted">Фото, скетчи, примеры — для себя и для перерисовки.</div>
        ) : (
          <div className="gallery char-refs">
            {refs.map((rel) => (
              <button key={rel} className="thumb" onClick={() => setZoom(rel)} title={rel}>
                <img src={charFileUrl(c.id, rel, 320)} loading="lazy" alt={rel} />
                <span>{rel.split("/").pop()}</span>
              </button>
            ))}
          </div>
        )}
      </Card>

      {variants.length > 0 && !(taskId && (c.task?.status !== "done" || !c.turnaround_path)) && (
        <Card title={`Варианты генерации (${variants.length})`}>
          {taskId ? (
            <CharacterGeneration characterId={c.id} taskId={taskId} />
          ) : (
            <div className="gallery">
              {variants.map((rel) => (
                <button key={rel} className="thumb" onClick={() => setZoom(rel)}>
                  <img src={charFileUrl(c.id, rel, 320)} loading="lazy" alt={rel} />
                </button>
              ))}
            </div>
          )}
        </Card>
      )}

      <Card title="Описание и голос">
        <EditForm c={c} />
      </Card>

      {zoom && (
        <div className="char-zoom" onClick={() => setZoom(null)} role="dialog" aria-label="Просмотр">
          <img src={charFileUrl(c.id, zoom)} alt={zoom} />
        </div>
      )}
    </div>
  );
}
