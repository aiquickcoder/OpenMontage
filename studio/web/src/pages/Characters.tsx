import { useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { Card, Empty, ErrorNote } from "../components/ui";
import { CharacterGeneration } from "../components/CharacterGeneration";
import {
  charFileUrl,
  ROLE_LABEL,
  TASK_LABEL,
  type Character,
  type CharacterRole,
  type GenerateResult,
} from "../types/characters";
import "../characters.css";

const ROLES = Object.keys(ROLE_LABEL) as CharacterRole[];

function NewCharacter() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [name, setName] = useState("");
  const [role, setRole] = useState<CharacterRole>("hero");
  const [description, setDescription] = useState("");
  const [voice, setVoice] = useState("");
  const [language, setLanguage] = useState("ru");
  const [style, setStyle] = useState("");
  const [variants, setVariants] = useState(2);
  const [started, setStarted] = useState<GenerateResult | null>(null);

  const body = () => ({
    name: name.trim(),
    role,
    description: description.trim(),
    voice: { language, description: voice.trim() },
    style_lock: style.trim(),
  });
  const done = () => {
    qc.invalidateQueries({ queryKey: ["characters"] });
  };
  const generate = useMutation({
    mutationFn: () => api.post<GenerateResult>("/characters/generate", { ...body(), variants }),
    onSuccess: (r) => {
      setStarted(r);
      done();
    },
  });
  const manual = useMutation({
    mutationFn: () => api.post<Character>("/characters", body()),
    onSuccess: (c) => {
      done();
      nav(`/characters/${c.id}`);
    },
  });
  const valid = name.trim().length > 0 && description.trim().length >= 10;
  const busy = generate.isPending || manual.isPending;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    generate.mutate();
  };

  return (
    <Card title="Новый персонаж">
      <form className="form" onSubmit={submit}>
        <label>
          Имя
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Кот Барсик" maxLength={80} />
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
          <textarea
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Рыжий пухлый кот в белом поварском колпаке и клетчатом фартуке, зелёные глаза, пышные усы"
            maxLength={2000}
          />
        </label>
        <label>
          Голос
          <input value={voice} onChange={(e) => setVoice(e.target.value)} placeholder="Мужской, бархатный, неторопливый" />
        </label>
        <label>
          Язык голоса
          <input value={language} onChange={(e) => setLanguage(e.target.value)} placeholder="ru" maxLength={20} />
        </label>
        <label className="span-2">
          Стиль (style lock)
          <input value={style} onChange={(e) => setStyle(e.target.value)} placeholder="По умолчанию: 3D в духе Pixar, мягкий свет, сочные цвета" />
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
        <div className="form-foot">
          <button type="button" className="btn" disabled={!valid || busy} onClick={() => manual.mutate()} title="Без генерации: загрузите лист сами">
            Создать вручную
          </button>
          <button className="btn btn-primary" disabled={!valid || busy}>
            {generate.isPending ? "Ставлю…" : "Нарисовать в Flow"}
          </button>
        </div>
        <div className="span-2">
          <ErrorNote error={generate.error || manual.error} />
        </div>
      </form>
      {started && (
        <div className="char-gen-box">
          <h3>
            <Link to={`/characters/${started.id}`}>{started.character.name}</Link>
          </h3>
          <CharacterGeneration characterId={started.id} taskId={started.task_id} onRestart={setStarted} />
        </div>
      )}
    </Card>
  );
}

function CharacterCard({ c }: { c: Character }) {
  const tags = c.library.tags ?? [];
  return (
    <Link to={`/characters/${c.id}`} className={`char-card${c.library.archived ? " char-archived" : ""}`}>
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
          {c.library.archived && <span className="badge">в архиве</span>}
          {c.task && c.task.status !== "done" && <span className={`badge char-task-${c.task.status}`}>{TASK_LABEL[c.task.status]}</span>}
        </div>
        {tags.length > 0 && (
          <div className="char-card-meta">
            {tags.slice(0, 4).map((t) => (
              <span key={t} className="char-tag">
                {t}
              </span>
            ))}
          </div>
        )}
      </div>
    </Link>
  );
}

export function CharactersPage() {
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const tag = params.get("tag") ?? "";
  const archived = params.get("archived") === "1" ? "1" : "0";
  const [search, setSearch] = useState(q);
  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    setParams(next, { replace: true });
  };

  const { data, error } = useQuery({
    queryKey: ["characters", q, tag, archived],
    queryFn: () =>
      api.get<Character[]>(`/characters?${new URLSearchParams({ q, tag, archived }).toString()}`),
    refetchInterval: (query) =>
      query.state.data?.some((c) => c.task && ["queued", "running"].includes(c.task.status)) ? 8000 : false,
  });
  // tag chips come from an unfiltered (by tag) listing so they don't vanish when one is picked
  const all = useQuery({
    queryKey: ["characters", "", "", archived],
    queryFn: () => api.get<Character[]>(`/characters?archived=${archived}`),
  });
  const tags = useMemo(() => {
    const set = new Set<string>();
    all.data?.forEach((c) => c.library.tags?.forEach((t) => set.add(t)));
    return [...set].sort((a, b) => a.localeCompare(b, "ru"));
  }, [all.data]);

  return (
    <div className="page">
      <header className="page-head">
        <h1>Персонажи</h1>
      </header>
      <NewCharacter />
      <Card
        title="Библиотека"
        actions={
          <div className="chips">
            <button className={`chip${archived === "0" ? " chip-on" : ""}`} onClick={() => set({ archived: "" })}>
              Активные
            </button>
            <button className={`chip${archived === "1" ? " chip-on" : ""}`} onClick={() => set({ archived: "1" })}>
              Архив
            </button>
          </div>
        }
      >
        <form
          className="char-toolbar"
          onSubmit={(e) => {
            e.preventDefault();
            set({ q: search.trim() });
          }}
        >
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onBlur={() => set({ q: search.trim() })}
            placeholder="Поиск по имени, внешности, тегам"
            aria-label="Поиск персонажей"
          />
          {tags.length > 0 && (
            <div className="chips">
              <button type="button" className={`chip${!tag ? " chip-on" : ""}`} onClick={() => set({ tag: "" })}>
                Все теги
              </button>
              {tags.map((t) => (
                <button type="button" key={t} className={`chip${tag === t ? " chip-on" : ""}`} onClick={() => set({ tag: t })}>
                  {t}
                </button>
              ))}
            </div>
          )}
        </form>
        <ErrorNote error={error} />
        {data && data.length === 0 && <Empty>{q || tag ? "Ничего не найдено" : archived === "1" ? "Архив пуст" : "Пока нет персонажей"}</Empty>}
        {data && data.length > 0 && (
          <div className="char-grid">
            {data.map((c) => (
              <CharacterCard key={c.id} c={c} />
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
