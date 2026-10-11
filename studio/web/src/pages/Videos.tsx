import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type VideoRow } from "../api";
import { Card, Empty, ErrorNote } from "../components/ui";
import { PublishDialog } from "../components/PublishDialog";
import { ago, shortId } from "../format";

const fileUrl = (id: string, name: string, w?: number) =>
  `/api/videos/${encodeURIComponent(id)}/file/${encodeURIComponent(name)}${w ? `?w=${w}` : ""}`;

const VIDEO_STATE: Record<VideoRow["state"], string> = {
  awaiting_approval: "Ждёт одобрения",
  approved: "Одобрен",
  rejected: "Отклонён",
};

function VideoPanel({ video, onClose }: { video: VideoRow; onClose: () => void }) {
  const qc = useQueryClient();
  const [caption, setCaption] = useState(video.caption);
  const [tags, setTags] = useState(video.hashtags.join(" "));
  useEffect(() => {
    setCaption(video.caption);
    setTags(video.hashtags.join(" "));
  }, [video.id, video.caption, video.hashtags]);
  const pending = video.state === "awaiting_approval";
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["videos"] });
    qc.invalidateQueries({ queryKey: ["overview"] });
  };
  const save = useMutation({
    mutationFn: () =>
      api.patch(`/videos/${encodeURIComponent(video.id)}`, { caption, hashtags: tags.split(/[\s,]+/).filter(Boolean) }),
    onSuccess: refresh,
  });
  const decide = useMutation({
    mutationFn: (action: "approve" | "reject") => api.post(`/videos/${encodeURIComponent(video.id)}/${action}`),
    onSuccess: refresh,
  });
  const dirty = caption !== video.caption || tags !== video.hashtags.join(" ");
  const [publishing, setPublishing] = useState(false);

  return (
    <>
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()}>
        <header className="drawer-head">
          <div>
            <h2>{shortId(video.id)}</h2>
            <span className="muted">
              {VIDEO_STATE[video.state]} · {video.duration ? `${video.duration} с` : ""}
            </span>
          </div>
          <button className="btn btn-ghost" onClick={onClose} aria-label="Закрыть">
            ✕
          </button>
        </header>
        <div className="drawer-body">
          {video.available ? (
            <video className="player" src={fileUrl(video.id, "final.mp4")} controls autoPlay playsInline />
          ) : (
            <Empty>Файлы ролика удалены</Empty>
          )}
          <p className="muted clamp-3">{video.text}</p>
          <label>
            Подпись
            <textarea rows={5} value={caption} disabled={!pending} onChange={(e) => setCaption(e.target.value)} />
          </label>
          <label>
            Хэштеги
            <input value={tags} disabled={!pending} onChange={(e) => setTags(e.target.value)} placeholder="кот пицца доставка" />
          </label>
          <ErrorNote error={save.error || decide.error} />
          <div className="drawer-actions">
            {pending && (
              <>
                <button className="btn" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
                  {save.isPending ? "Сохраняю…" : "Сохранить подпись"}
                </button>
                <button className="btn btn-danger" disabled={decide.isPending} onClick={() => decide.mutate("reject")}>
                  ❌ Отклонить
                </button>
                <button
                  className="btn btn-primary"
                  disabled={decide.isPending || dirty}
                  title={dirty ? "Сначала сохраните подпись" : ""}
                  onClick={() => decide.mutate("approve")}
                >
                  ✅ Одобрить
                </button>
              </>
            )}
            {video.state === "approved" && (
              <button className="btn btn-primary" onClick={() => setPublishing(true)}>
                Опубликовать
              </button>
            )}
            {video.available && (
              <a className="btn btn-ghost" href={fileUrl(video.id, "final.mp4")} download>
                Скачать
              </a>
            )}
            <Link className="btn btn-ghost" to={`/briefs/${video.id}`}>
              Как делался
            </Link>
          </div>
        </div>
      </aside>
    </div>
    {publishing && <PublishDialog briefId={video.id} onClose={() => setPublishing(false)} />}
    </>
  );
}

export function VideosPage() {
  const [params, setParams] = useSearchParams();
  const open = params.get("open");
  const { data, error } = useQuery({ queryKey: ["videos"], queryFn: () => api.get<VideoRow[]>("/videos") });
  const current = data?.find((v) => v.id === open) ?? null;
  const groups: [string, VideoRow[]][] = [
    ["Ждут одобрения", (data ?? []).filter((v) => v.state === "awaiting_approval")],
    ["Одобренные", (data ?? []).filter((v) => v.state === "approved")],
    ["Отклонённые", (data ?? []).filter((v) => v.state === "rejected")],
  ];

  return (
    <div className="page">
      <header className="page-head">
        <h1>Видео</h1>
      </header>
      <ErrorNote error={error} />
      {data && data.length === 0 && <Empty>Готовых роликов пока нет</Empty>}
      {groups
        .filter(([, rows]) => rows.length > 0)
        .map(([title, rows]) => (
          <Card key={title} title={`${title} (${rows.length})`}>
            <div className="video-grid">
              {rows.map((v) => (
                <button key={v.id} className="video-card" onClick={() => setParams({ open: v.id })}>
                  <div className="poster">
                    {v.available ? <img src={fileUrl(v.id, "final.mp4", 320)} loading="lazy" alt="" /> : <span>нет файла</span>}
                    {v.duration && <span className="poster-dur">{Math.round(v.duration)} с</span>}
                  </div>
                  <strong>{shortId(v.id)}</strong>
                  <span className="muted clamp">{v.caption || v.text}</span>
                  <span className="muted">{ago(v.updated_at)}</span>
                </button>
              ))}
            </div>
          </Card>
        ))}
      {current && <VideoPanel video={current} onClose={() => setParams({})} />}
    </div>
  );
}
