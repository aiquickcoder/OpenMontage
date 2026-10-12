import { useEffect, useState, type ChangeEvent, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { Card, ErrorNote } from "../components/ui";
import { PlanWeekForm, TaskLine, useStudioTask } from "../components/WorldTasks";
import { dateTime } from "../format";
import { charFileUrl, ROLE_LABEL, TASK_LABEL, type CharacterRole, type GenerateResult } from "../types/characters";
import {
  UPLOAD_KIND_LABEL,
  uploadWorldImage,
  WORLD_FIELD_LABEL,
  WORLD_FIELD_MAX,
  worldFileUrl,
  type ApplyDraftCharacter,
  type RefKind,
  type UploadKind,
  type World,
  type WorldFields,
} from "../types/worlds";
import "../characters.css";
import "../ads.css";
import "../worlds.css";

const ROLES = Object.keys(ROLE_LABEL) as CharacterRole[];
const REF_KINDS: RefKind[] = ["style", "location", "family"];
const UPLOAD_KINDS: UploadKind[] = ["cover", "style", "location", "family"];
const FIELD_KEYS = Object.keys(WORLD_FIELD_LABEL) as (keyof WorldFields)[];
const LONG: (keyof WorldFields)[] = ["logline", "audience", "setting", "style_lock", "negative_style", "episode_rules"];
const ACCEPT = "image/png,image/jpeg,image/webp";

const worldPath = (id: string) => `/worlds/${encodeURIComponent(id)}`;

function toFields(w: Partial<WorldFields>): WorldFields {
  const out = {} as WorldFields;
  for (const k of FIELD_KEYS) out[k] = w[k] ?? "";
  return out;
}

/** Inputs for the world text fields (shared by the edit form and the draft panel). */
function FieldInputs({ value, onChange, keys = FIELD_KEYS }: { value: WorldFields; onChange: (v: WorldFields) => void; keys?: (keyof WorldFields)[] }) {
  return (
    <>
      {keys.map((k) => {
        const props = {
          value: value[k],
          maxLength: WORLD_FIELD_MAX[k],
          onChange: (e: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => onChange({ ...value, [k]: e.target.value }),
        };
        const long = LONG.includes(k);
        return (
          <label key={k} className={long ? "span-2" : undefined}>
            {WORLD_FIELD_LABEL[k]}
            {long ? <textarea rows={k === "setting" || k === "episode_rules" ? 4 : 2} {...props} /> : <input {...props} />}
          </label>
        );
      })}
    </>
  );
}

function EditForm({ w }: { w: World }) {
  const qc = useQueryClient();
  const [form, setForm] = useState(() => toFields(w));
  useEffect(() => setForm(toFields(w)), [w]);
  const save = useMutation({
    mutationFn: () => {
      const body: Partial<WorldFields> = {};
      for (const k of FIELD_KEYS) body[k] = form[k].trim();
      return api.patch<World>(worldPath(w.id), body);
    },
    onSuccess: (r) => {
      qc.setQueryData(["world", w.id], r);
      qc.invalidateQueries({ queryKey: ["worlds"] });
    },
  });
  const dirty = JSON.stringify(form) !== JSON.stringify(toFields(w));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };
  return (
    <form className="form" onSubmit={submit}>
      <FieldInputs value={form} onChange={setForm} />
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

function References({ w, onZoom }: { w: World; onZoom: (rel: string) => void }) {
  const qc = useQueryClient();
  const [kind, setKind] = useState<UploadKind>("style");
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const setWorld = (r: World) => {
    qc.setQueryData(["world", w.id], r);
    qc.invalidateQueries({ queryKey: ["worlds"] });
  };
  const upload = useMutation({
    mutationFn: async (files: File[]) => {
      let last: World | null = null;
      // one file per request, sequentially (cover keeps only the last one)
      for (let i = 0; i < files.length; i++) {
        setProgress({ done: i, total: files.length });
        last = (await uploadWorldImage(w.id, kind, files[i])).world;
        setWorld(last);
      }
      return last;
    },
    onSettled: () => setProgress(null),
  });
  const remove = useMutation({
    mutationFn: (rel: string) => api.del<World>(`${worldPath(w.id)}/refs/${rel.split("/").map(encodeURIComponent).join("/")}`),
    onSuccess: setWorld,
  });
  const pick = (e: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    if (files.length) upload.mutate(kind === "cover" ? files.slice(-1) : files);
  };
  const del = (rel: string) => {
    if (window.confirm("Удалить картинку?")) remove.mutate(rel);
  };

  return (
    <Card
      title={`Референсы (${w.refs.length + (w.cover ? 1 : 0)})`}
      actions={
        <div className="world-upload">
          <select value={kind} onChange={(e) => setKind(e.target.value as UploadKind)} aria-label="Тип картинки" disabled={upload.isPending}>
            {UPLOAD_KINDS.map((k) => (
              <option key={k} value={k}>
                {UPLOAD_KIND_LABEL[k]}
              </option>
            ))}
          </select>
          <label className="btn char-upload">
            {progress ? `Загружаю ${progress.done + 1}/${progress.total}…` : "Загрузить"}
            <input type="file" accept={ACCEPT} multiple={kind !== "cover"} onChange={pick} disabled={upload.isPending} />
          </label>
        </div>
      }
    >
      <ErrorNote error={upload.error || remove.error} />
      <div className="world-cover">
        <div className="world-cover-img">
          {w.cover ? <img src={worldFileUrl(w.id, w.cover, 960)} alt="Обложка" onClick={() => onZoom(w.cover)} /> : <span>Нет обложки</span>}
          {w.cover && (
            <button type="button" className="ads-del" style={{ position: "absolute", top: 6, right: 6 }} disabled={remove.isPending} onClick={() => del(w.cover)}>
              ✕
            </button>
          )}
        </div>
        <div className="muted" style={{ flex: "1 1 200px" }}>
          Обложка, «Персонажи / семья» и «Стиль» уходят в генерацию листов персонажей этого мира как референсы.
        </div>
      </div>
      {REF_KINDS.map((k) => {
        const refs = w.refs.filter((r) => r.kind === k);
        if (refs.length === 0) return null;
        return (
          <div key={k} className="world-ref-group">
            <h3>
              {UPLOAD_KIND_LABEL[k]} ({refs.length})
            </h3>
            <div className="gallery">
              {refs.map((r) => (
                <div key={r.path} className="ads-thumb">
                  <button type="button" className="thumb" onClick={() => onZoom(r.path)} title={r.name || r.path}>
                    <img src={worldFileUrl(w.id, r.path, 320)} loading="lazy" alt={r.name || r.path} />
                    <span>{r.name || r.path.split("/").pop()}</span>
                  </button>
                  <button type="button" className="ads-del" disabled={remove.isPending} onClick={() => del(r.path)} aria-label="Удалить">
                    ✕
                  </button>
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </Card>
  );
}

const emptyDraftChar = (): ApplyDraftCharacter => ({
  name: "",
  role: "hero",
  description: "",
  prompt_tokens: "",
  voice: { language: "ru", description: "" },
  generate: true,
});

function DraftCharacterEditor({
  c,
  index,
  onChange,
  onRemove,
}: {
  c: ApplyDraftCharacter;
  index: number;
  onChange: (c: ApplyDraftCharacter) => void;
  onRemove: () => void;
}) {
  const set = (patch: Partial<ApplyDraftCharacter>) => onChange({ ...c, ...patch });
  const invalid = !c.name.trim() || c.description.trim().length < 10;
  return (
    <div className={`draft-char${c.generate ? "" : " draft-char-off"}`}>
      <div className="form">
        <label>
          Имя
          <input value={c.name} onChange={(e) => set({ name: e.target.value })} maxLength={80} />
        </label>
        <label>
          Роль
          <select value={c.role} onChange={(e) => set({ role: e.target.value })}>
            {!(c.role in ROLE_LABEL) && <option value={c.role}>{c.role}</option>}
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABEL[r]}
              </option>
            ))}
          </select>
        </label>
        <label className="span-2">
          Внешность
          <textarea rows={3} value={c.description} onChange={(e) => set({ description: e.target.value })} maxLength={2000} />
        </label>
        <label className="span-2">
          Токены промпта
          <textarea rows={2} value={c.prompt_tokens} onChange={(e) => set({ prompt_tokens: e.target.value })} maxLength={400} />
        </label>
        <label>
          Голос
          <input value={c.voice.description} onChange={(e) => set({ voice: { ...c.voice, description: e.target.value } })} maxLength={600} />
        </label>
        <label>
          Язык голоса
          <input value={c.voice.language} onChange={(e) => set({ voice: { ...c.voice, language: e.target.value } })} maxLength={20} />
        </label>
        <div className="span-2 char-actions" style={{ justifyContent: "space-between" }}>
          <label className="draft-check">
            <input type="checkbox" checked={c.generate} onChange={(e) => set({ generate: e.target.checked })} />
            Сгенерировать разворот
          </label>
          <button type="button" className="btn btn-danger" onClick={onRemove} aria-label={`Убрать персонажа ${index + 1}`}>
            Убрать
          </button>
        </div>
        {invalid && <div className="span-2 field-error">Нужны имя и описание внешности (от 10 символов).</div>}
      </div>
    </div>
  );
}

function DraftPanel({ w }: { w: World }) {
  const qc = useQueryClient();
  const draft = w.draft!;
  const [fields, setFields] = useState(() => toFields({ ...w, ...draft, name: draft.name || w.name }));
  const [chars, setChars] = useState<ApplyDraftCharacter[]>(() =>
    draft.characters.map((c) => ({
      ...emptyDraftChar(),
      ...c,
      voice: { language: c.voice?.language || "ru", description: c.voice?.description ?? "" },
      generate: true,
    })),
  );
  const [variants, setVariants] = useState(2);
  const apply = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = {};
      for (const k of FIELD_KEYS) body[k] = fields[k].trim();
      if (!fields.name.trim()) delete body.name;
      return api.post<{ world: World; characters: string[] }>(`${worldPath(w.id)}/apply-draft`, {
        ...body,
        characters: chars.map((c) => ({
          ...c,
          name: c.name.trim(),
          description: c.description.trim(),
          prompt_tokens: c.prompt_tokens.trim(),
          voice: { language: c.voice.language.trim() || "ru", description: c.voice.description.trim() },
        })),
        variants,
      });
    },
    onSuccess: (r) => {
      qc.setQueryData(["world", w.id], r.world);
      qc.invalidateQueries({ queryKey: ["worlds"] });
      qc.invalidateQueries({ queryKey: ["characters"] });
    },
  });
  const invalid = chars.some((c) => !c.name.trim() || c.description.trim().length < 10);
  const generating = chars.filter((c) => c.generate).length;

  return (
    <Card title="Черновик разбора">
      <p className="muted" style={{ marginTop: 0 }}>
        Claude разобрал исходный промпт. Проверьте поля и персонажей — «Создать персонажей» сохранит поля в мир, создаст персонажей и
        поставит их развороты в очередь Flow.
      </p>
      <div className="form">
        <FieldInputs value={fields} onChange={setFields} keys={FIELD_KEYS.filter((k) => k !== "label" && k !== "duration")} />
      </div>
      <h3 style={{ fontSize: 15, margin: "16px 0 10px" }}>Персонажи ({chars.length})</h3>
      <div className="draft-chars">
        {chars.map((c, i) => (
          <DraftCharacterEditor
            key={i}
            c={c}
            index={i}
            onChange={(next) => setChars(chars.map((x, j) => (j === i ? next : x)))}
            onRemove={() => setChars(chars.filter((_, j) => j !== i))}
          />
        ))}
        {chars.length < 8 && (
          <div>
            <button type="button" className="btn" onClick={() => setChars([...chars, emptyDraftChar()])}>
              + Персонаж
            </button>
          </div>
        )}
      </div>
      <div className="form-foot world-gap" style={{ flexWrap: "wrap" }}>
        <ErrorNote error={apply.error} />
        <label style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          Вариантов листа
          <select value={variants} onChange={(e) => setVariants(Number(e.target.value))} style={{ width: "auto" }}>
            {[1, 2, 3, 4].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="btn btn-primary" disabled={invalid || apply.isPending} onClick={() => apply.mutate()}>
          {apply.isPending ? "Создаю…" : `Создать персонажей${generating ? ` (рисовать: ${generating})` : ""}`}
        </button>
      </div>
    </Card>
  );
}

function ExtractPanel({ w }: { w: World }) {
  const qc = useQueryClient();
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["world", w.id] });
    qc.invalidateQueries({ queryKey: ["worlds"] });
  };
  const extract = useMutation({
    mutationFn: () => api.post<{ task_id: string }>(`${worldPath(w.id)}/extract`),
    onSuccess: refresh,
  });
  const active = !!w.task && ["queued", "running"].includes(w.task.status);
  const isExtract = w.task?.kind === "world_extract";
  const task = useStudioTask(active ? w.task_id : null, refresh);
  if (!w.source_prompt && !active) return null;
  if (w.draft && !active) return null;
  return (
    <Card title="Разбор промпта">
      {active && <TaskLine task={task.data} running={isExtract ? "Claude разбирает промпт в поля мира и персонажей…" : "Задача выполняется…"} />}
      {!active && w.task?.status === "failed" && <div className="error-note">Прошлый разбор: {w.task.error}</div>}
      {!active && (
        <div className="char-actions world-gap">
          <button className="btn btn-primary" disabled={extract.isPending} onClick={() => extract.mutate()}>
            {extract.isPending ? "Запускаю…" : "Разобрать промпт (Claude)"}
          </button>
          <span className="muted">Заполнит черновик полей и персонажей из исходного промпта.</span>
        </div>
      )}
      <ErrorNote error={extract.error} />
    </Card>
  );
}

function AddCharacter({ w, onDone }: { w: World; onDone: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [role, setRole] = useState<CharacterRole>("hero");
  const [description, setDescription] = useState("");
  const [voice, setVoice] = useState("");
  const [variants, setVariants] = useState(2);
  const gen = useMutation({
    mutationFn: () =>
      api.post<GenerateResult>("/characters/generate", {
        name: name.trim(),
        role,
        description: description.trim(),
        voice: { language: "ru", description: voice.trim() },
        style_lock: "",
        tags: [w.name.slice(0, 40)],
        world_id: w.id,
        variants,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["world", w.id] });
      qc.invalidateQueries({ queryKey: ["characters"] });
      qc.invalidateQueries({ queryKey: ["worlds"] });
      onDone();
    },
  });
  const valid = name.trim().length > 0 && description.trim().length >= 10;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    gen.mutate();
  };
  return (
    <form className="form char-gen-box" onSubmit={submit}>
      <label>
        Имя
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} />
      </label>
      <label>
        Роль
        <select value={role} onChange={(e) => setRole(e.target.value as CharacterRole)}>
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {ROLE_LABEL[r]}
            </option>
          ))}
        </select>
      </label>
      <label className="span-2">
        Внешность
        <textarea rows={3} value={description} onChange={(e) => setDescription(e.target.value)} maxLength={2000} />
      </label>
      <label>
        Голос
        <input value={voice} onChange={(e) => setVoice(e.target.value)} maxLength={600} placeholder="Детский, звонкий" />
      </label>
      <label>
        Вариантов листа
        <select value={variants} onChange={(e) => setVariants(Number(e.target.value))}>
          {[1, 2, 3, 4].map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
      </label>
      <div className="span-2 form-foot">
        <ErrorNote error={gen.error} />
        <span className="muted">Стиль — из мира.</span>
        <button className="btn btn-primary" disabled={!valid || gen.isPending}>
          {gen.isPending ? "Ставлю…" : "Нарисовать в Flow"}
        </button>
      </div>
    </form>
  );
}

function WorldCharacters({ w }: { w: World }) {
  const [adding, setAdding] = useState(false);
  const chars = w.characters ?? [];
  return (
    <Card
      title={`Персонажи мира (${chars.length})`}
      actions={
        <button className="btn" onClick={() => setAdding(!adding)}>
          {adding ? "Отмена" : "Добавить персонажа"}
        </button>
      }
    >
      {chars.length === 0 && !adding && <div className="muted">Пока нет персонажей. Разберите промпт или добавьте вручную.</div>}
      {chars.length > 0 && (
        <div className="char-grid">
          {chars.map((c) => (
            <Link key={c.id} to={`/characters/${c.id}`} className="char-card">
              <div className="char-card-img">
                {c.turnaround_path ? (
                  <img src={charFileUrl(c.id, c.turnaround_path, 320)} alt="" loading="lazy" />
                ) : (
                  <span>{c.task ? TASK_LABEL[c.task.status] : "Нет листа"}</span>
                )}
              </div>
              <div className="char-card-body">
                <div className="char-card-name">{c.name}</div>
                <div className="char-card-meta">
                  <span>{ROLE_LABEL[c.role as CharacterRole] ?? c.role}</span>
                  {c.task && c.task.status !== "done" && <span className={`badge char-task-${c.task.status}`}>{TASK_LABEL[c.task.status]}</span>}
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}
      {adding && <AddCharacter w={w} onDone={() => setAdding(false)} />}
    </Card>
  );
}

export function WorldPage() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const [zoom, setZoom] = useState<string | null>(null);
  const { data: w, error } = useQuery({
    queryKey: ["world", id],
    queryFn: () => api.get<World>(worldPath(id)),
    refetchInterval: (q) =>
      q.state.data?.characters?.some((c) => c.task && ["queued", "running"].includes(c.task.status)) ? 8000 : false,
  });
  const archive = useMutation({
    mutationFn: (archived: boolean) => api.patch<World>(worldPath(id), { archived }),
    onSuccess: (r) => {
      qc.setQueryData(["world", id], r);
      qc.invalidateQueries({ queryKey: ["worlds"] });
    },
  });

  if (error) return <ErrorNote error={error} />;
  if (!w) return <div className="loading">Загрузка…</div>;
  const chars = w.characters ?? [];

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <Link to="/worlds" className="muted">
            ← Миры
          </Link>
          <h1>{w.name}</h1>
          <div className="muted">
            {w.label && `${w.label} · `}персонажей: {chars.length} · обновлён {dateTime(w.updated_at)}
            {w.archived && " · в архиве"}
          </div>
        </div>
        <div className="char-actions">
          <Link className="btn" to={`/planner?world=${encodeURIComponent(w.id)}`}>
            План эпизодов
          </Link>
          <button className={w.archived ? "btn" : "btn btn-danger"} disabled={archive.isPending} onClick={() => archive.mutate(!w.archived)}>
            {w.archived ? "Вернуть из архива" : "В архив"}
          </button>
        </div>
      </header>
      <ErrorNote error={archive.error} />

      <ExtractPanel w={w} />
      {w.draft && <DraftPanel key={JSON.stringify(w.draft)} w={w} />}

      <WorldCharacters w={w} />

      <Card title="Спланировать неделю (ИИ)">
        {chars.length === 0 ? (
          <div className="muted">Сначала создайте персонажей мира — план пишется под них.</div>
        ) : (
          <PlanWeekForm worldId={w.id} />
        )}
      </Card>

      <References w={w} onZoom={setZoom} />

      <Card title="Описание мира">
        <EditForm w={w} />
      </Card>

      {w.source_prompt && (
        <Card>
          <details>
            <summary style={{ marginTop: 0 }}>Исходный промпт</summary>
            <div className="log draft-source world-gap">{w.source_prompt}</div>
          </details>
        </Card>
      )}

      {zoom && (
        <div className="char-zoom" onClick={() => setZoom(null)} role="dialog" aria-label="Просмотр">
          <img src={worldFileUrl(w.id, zoom)} alt={zoom} />
        </div>
      )}
    </div>
  );
}
