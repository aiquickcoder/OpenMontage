import { useState, type ChangeEvent, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import { Card, Empty, ErrorNote } from "../components/ui";
import { MAX_ARCHIVE, uploadRaw, WORLD_TASK_LABEL, worldFileUrl, type World } from "../types/worlds";
import "../characters.css";
import "../worlds.css";

function NewWorld() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [name, setName] = useState("");
  const [style, setStyle] = useState("");
  const create = useMutation({
    mutationFn: () => api.post<World>("/worlds", { name: name.trim(), style_lock: style.trim() }),
    onSuccess: (w) => {
      qc.invalidateQueries({ queryKey: ["worlds"] });
      nav(`/worlds/${w.id}`);
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };
  return (
    <form className="form" onSubmit={submit}>
      <label>
        Название
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Котики на кухне" maxLength={80} />
      </label>
      <label>
        Стиль (style lock)
        <input value={style} onChange={(e) => setStyle(e.target.value)} placeholder="3D в духе Pixar, мягкий свет" maxLength={1500} />
      </label>
      <div className="span-2 form-foot">
        <ErrorNote error={create.error} />
        <button className="btn btn-primary" disabled={!name.trim() || create.isPending}>
          {create.isPending ? "Создаю…" : "Создать мир"}
        </button>
      </div>
    </form>
  );
}

function ImportArchive() {
  const qc = useQueryClient();
  const [progress, setProgress] = useState(0);
  const imp = useMutation({
    mutationFn: (file: File) => {
      if (file.size > MAX_ARCHIVE) throw new ApiError(413, "Архив больше 200 МБ");
      setProgress(0);
      return uploadRaw<{ ids: string[] }>("/worlds/import?extract=1", file, "application/zip", setProgress);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["worlds"] }),
  });
  const pick = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (file) imp.mutate(file);
  };
  return (
    <div className="world-task">
      <div className="world-import">
        <label className="btn char-upload">
          {imp.isPending ? (progress < 1 ? `Загружаю… ${Math.round(progress * 100)}%` : "Распаковываю…") : "Импорт архива (.zip)"}
          <input type="file" accept=".zip,application/zip" onChange={pick} disabled={imp.isPending} />
        </label>
        {imp.isPending && (
          <div className="progress progress-active">
            <div className="progress-bar" style={{ width: `${Math.max(5, Math.round(progress * 100))}%` }} />
          </div>
        )}
      </div>
      <span className="muted">Архив с папками миров (промпты, картинки). После импорта Claude разберёт промпты в черновики.</span>
      <ErrorNote error={imp.error} />
      {imp.isSuccess && <span className="muted">Импортировано миров: {imp.data.ids.length}. Разбор запущен.</span>}
    </div>
  );
}

function WorldCard({ w }: { w: World }) {
  return (
    <Link to={`/worlds/${w.id}`} className={`world-card${w.archived ? " world-archived" : ""}`}>
      <div className="world-card-img">
        {w.cover ? <img src={worldFileUrl(w.id, w.cover, 480)} alt="" loading="lazy" /> : <span>Нет обложки</span>}
      </div>
      <div className="world-card-body">
        <div className="world-card-name">{w.name}</div>
        {w.logline && <div className="muted clamp-3">{w.logline}</div>}
        <div className="world-card-meta">
          <span>Персонажей: {w.character_count}</span>
          {w.draft && <span className="badge">черновик разбора</span>}
          {w.archived && <span className="badge">в архиве</span>}
          {w.task && w.task.status !== "done" && (
            <span className={`badge char-task-${w.task.status}`}>{WORLD_TASK_LABEL[w.task.status]}</span>
          )}
        </div>
      </div>
    </Link>
  );
}

export function WorldsPage() {
  const [params, setParams] = useSearchParams();
  const archived = params.get("archived") === "1" ? "1" : "0";
  const { data, error } = useQuery({
    queryKey: ["worlds", archived],
    queryFn: () => api.get<World[]>(`/worlds?archived=${archived}`),
    refetchInterval: (q) => (q.state.data?.some((w) => w.task && ["queued", "running"].includes(w.task.status)) ? 8000 : false),
  });

  return (
    <div className="page">
      <header className="page-head">
        <h1>Миры</h1>
      </header>
      <Card title="Новый мир">
        <NewWorld />
        <div className="world-gap">
          <ImportArchive />
        </div>
      </Card>
      <Card
        title="Сериалы"
        actions={
          <div className="chips">
            <button className={`chip${archived === "0" ? " chip-on" : ""}`} onClick={() => setParams({}, { replace: true })}>
              Активные
            </button>
            <button className={`chip${archived === "1" ? " chip-on" : ""}`} onClick={() => setParams({ archived: "1" }, { replace: true })}>
              Архив
            </button>
          </div>
        }
      >
        <ErrorNote error={error} />
        {!data && !error && <div className="muted">Загрузка…</div>}
        {data && data.length === 0 && <Empty>{archived === "1" ? "Архив пуст" : "Пока нет миров"}</Empty>}
        {data && data.length > 0 && (
          <div className="world-grid">
            {data.map((w) => (
              <WorldCard key={w.id} w={w} />
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
