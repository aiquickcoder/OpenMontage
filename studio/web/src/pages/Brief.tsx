import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type BriefDetail } from "../api";
import { Card, Empty, ErrorNote, StageRail, StateBadge } from "../components/ui";
import { ago, dateTime, STATE_LABEL, shortId } from "../format";

const fileUrl = (id: string, rel: string, w?: number) =>
  `/api/briefs/${encodeURIComponent(id)}/file/${rel.split("/").map(encodeURIComponent).join("/")}${w ? `?w=${w}` : ""}`;

export function BriefPage() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const [tab, setTab] = useState<"log" | "run">("log");
  const [zoom, setZoom] = useState<string | null>(null);
  const { data, error } = useQuery({
    queryKey: ["brief", id],
    queryFn: () => api.get<BriefDetail>(`/briefs/${encodeURIComponent(id)}`),
    refetchInterval: (q) => (q.state.data?.state === "running" ? 20_000 : false),
  });
  const act = useMutation({
    mutationFn: (action: "retry" | "cancel") => api.post(`/briefs/${encodeURIComponent(id)}/${action}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["brief", id] });
      qc.invalidateQueries({ queryKey: ["briefs"] });
    },
  });

  if (error) return <ErrorNote error={error} />;
  if (!data) return <div className="loading">Загрузка…</div>;
  const canRetry = data.state === "failed" || data.state === "budget";
  const canCancel = ["queued", "failed", "budget"].includes(data.state);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <Link to="/briefs" className="muted">
            ← Производство
          </Link>
          <h1>{shortId(data.id)}</h1>
          <div className="muted">
            {dateTime(data.created_at)} · <StateBadge state={data.state} />
          </div>
        </div>
        <div className="head-actions">
          {data.state === "awaiting_approval" && (
            <Link className="btn btn-primary" to={`/videos?open=${data.id}`}>
              Смотреть ролик
            </Link>
          )}
          {canRetry && (
            <button className="btn" disabled={act.isPending} onClick={() => act.mutate("retry")}>
              Продолжить с последнего этапа
            </button>
          )}
          {canCancel && (
            <button className="btn btn-danger" disabled={act.isPending} onClick={() => act.mutate("cancel")}>
              Отменить
            </button>
          )}
        </div>
      </header>
      <ErrorNote error={act.error} />

      <Card>
        <p className="brief-text">{data.text}</p>
        {Object.keys(data.options).length > 0 && (
          <div className="chips">
            {Object.entries(data.options).map(([k, v]) => (
              <span key={k} className="chip chip-static">
                {k}: {v}
              </span>
            ))}
          </div>
        )}
        {data.detail && <div className={`detail-note detail-${data.state}`}>{data.detail}</div>}
      </Card>

      <Card
        title="Этапы"
        actions={
          data.credits.budget !== undefined && (
            <span className="muted">
              Кредиты Flow: {data.credits.spent} из {data.credits.budget}
              {data.credits.reserved ? ` (+${data.credits.reserved} в резерве)` : ""}
            </span>
          )
        }
      >
        <StageRail stages={data.stages} />
      </Card>

      {data.images.length > 0 && (
        <Card title={`Картинки (${data.images.length})`}>
          <div className="gallery">
            {data.images.map((rel) => (
              <button key={rel} className="thumb" onClick={() => setZoom(rel)} title={rel}>
                <img src={fileUrl(data.id, rel, 320)} loading="lazy" alt={rel} />
                <span>{rel.split("/").pop()}</span>
              </button>
            ))}
          </div>
        </Card>
      )}

      {data.clips.length > 0 && (
        <Card title={`Клипы (${data.clips.length})`}>
          <div className="clips">
            {data.clips.map((rel) => (
              <figure key={rel}>
                <video src={fileUrl(data.id, rel)} controls preload="metadata" />
                <figcaption>{rel.split("/").pop()}</figcaption>
              </figure>
            ))}
          </div>
        </Card>
      )}

      <Card title="Вызовы Flow">
        {data.flow_calls.length === 0 ? (
          <Empty>Пока не было</Empty>
        ) : (
          <table className="table compact">
            <tbody>
              {[...data.flow_calls].reverse().map((c, i) => (
                <tr key={i}>
                  <td className="nowrap muted">{c.ts ? ago(c.ts) : ""}</td>
                  <td>{c.tool === "flow_veo_video" ? "Видео" : c.tool === "flow_image" ? "Картинка" : c.tool}</td>
                  <td>{c.scene_id}</td>
                  <td>{c.success ? <span className="ok">готово</span> : <span className="bad">ошибка</span>}</td>
                  <td className="muted nowrap">{c.duration_s ? `${Math.round(c.duration_s)} с` : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card
        title="Журнал"
        actions={
          <div className="chips">
            <button className={`chip${tab === "log" ? " chip-on" : ""}`} onClick={() => setTab("log")}>
              Автопилот
            </button>
            <button className={`chip${tab === "run" ? " chip-on" : ""}`} onClick={() => setTab("run")}>
              Итог прогона
            </button>
          </div>
        }
      >
        <pre className="log">{(tab === "log" ? data.autopilot_log : data.run_log) || "Пусто"}</pre>
        <details>
          <summary className="muted">История состояний ({data.events.length})</summary>
          <ul className="events">
            {data.events.map((e, i) => (
              <li key={i}>
                <span className="muted">{dateTime(e.at)}</span> {STATE_LABEL[e.state as keyof typeof STATE_LABEL] ?? e.state}
                {e.detail && !e.detail.startsWith("{") && <span className="muted"> — {e.detail}</span>}
              </li>
            ))}
          </ul>
        </details>
      </Card>

      {zoom && (
        <div className="lightbox" onClick={() => setZoom(null)}>
          <img src={fileUrl(data.id, zoom)} alt={zoom} />
        </div>
      )}
    </div>
  );
}
