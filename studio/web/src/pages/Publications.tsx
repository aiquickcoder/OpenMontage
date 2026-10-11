import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { Card, Empty, ErrorNote } from "../components/ui";
import { FarmBanner } from "../components/FarmBanner";
import { isHttpUrl, shortId } from "../format";
import { JOB_STATUS_LABEL, PLATFORM_SHORT, agoTs, commandNote, timeTs, useFarmRefresh } from "../farm";
import type { CommandResult, JobStatus, PublicationCell, PublicationsResponse } from "../types/farm";
import "../farm.css";

const STATUSES: (JobStatus | "")[] = ["", "pending", "running", "done", "failed", "cancelled"];

function cellTime(c: PublicationCell): string {
  if (c.status === "pending") return `старт ${agoTs(c.run_after)}`;
  if (c.status === "running") return `с ${timeTs(c.started_at)}`;
  return timeTs(c.finished_at ?? c.updated_at);
}

function Cell({ cell, onShot }: { cell: PublicationCell; onShot: (src: string) => void }) {
  const refresh = useFarmRefresh();
  const [note, setNote] = useState<string | null>(null);
  const act = useMutation({
    mutationFn: (action: "retry" | "cancel") => api.post<CommandResult>(`/publications/${cell.job_id}/${action}`),
    onSuccess: (res, action) => {
      setNote(commandNote(res, action === "retry" ? "Поставлено заново" : "Отменено"));
      refresh();
    },
  });
  const shots = cell.artifacts.filter((u) => /\.(png|jpe?g)$/i.test(u));
  const label = cell.status === "done" && !cell.confirmed ? "Опубликовано?" : JOB_STATUS_LABEL[cell.status];

  return (
    <div className={`pub-cell pub-${cell.status}`}>
      <span className={`badge pub-badge pub-badge-${cell.status}${cell.status === "done" && !cell.confirmed ? " pub-unconfirmed" : ""}`}
        title={cell.status === "done" && !cell.confirmed ? "Публикация прошла, но пост на странице не подтверждён" : ""}>
        {label}
        {cell.status === "done" && cell.confirmed && " ✓"}
      </span>
      <span className="muted pub-time">{cellTime(cell)}</span>
      {cell.attempts > 1 && <span className="muted pub-time">попытка {cell.attempts}</span>}
      {cell.error && <span className="pub-error" title={cell.error}>{cell.error}</span>}
      {isHttpUrl(cell.url) && (
        <a className="pub-link" href={cell.url} target="_blank" rel="noreferrer">
          открыть пост
        </a>
      )}
      {shots.length > 0 && (
        <button className="pub-shot" onClick={() => onShot(shots[0])} title="Скриншот в момент ошибки">
          <img src={shots[0]} alt="" loading="lazy" />
        </button>
      )}
      {(cell.status === "failed" || cell.status === "cancelled") && (
        <button className="btn btn-ghost pub-btn" disabled={act.isPending} onClick={() => act.mutate("retry")}>
          ↻ Повторить
        </button>
      )}
      {cell.status === "pending" && (
        <button className="btn btn-ghost pub-btn btn-danger" disabled={act.isPending} onClick={() => act.mutate("cancel")}>
          Отменить
        </button>
      )}
      {act.error && <ErrorNote error={act.error} />}
      {note && <span className="muted pub-time">{note}</span>}
    </div>
  );
}

export function PublicationsPage() {
  const [params, setParams] = useSearchParams();
  const release = params.get("release") ?? "";
  const account = params.get("account") ?? "";
  const status = params.get("status") ?? "";
  const [shot, setShot] = useState<string | null>(null);
  const qs = new URLSearchParams();
  if (release) qs.set("release_id", release);
  if (account) qs.set("account", account);
  if (status) qs.set("status", status);
  const { data, error } = useQuery({
    queryKey: ["publications", release, account, status],
    queryFn: () => api.get<PublicationsResponse>(`/publications${qs.toString() ? `?${qs}` : ""}`),
    refetchInterval: 15_000,
  });
  // releases for the filter come from an unfiltered view of the same data
  const all = useQuery({
    queryKey: ["publications", "", "", ""],
    queryFn: () => api.get<PublicationsResponse>("/publications"),
    enabled: !!(release || account || status),
  });
  const releases = (release || account || status ? all.data : data)?.rows.map((r) => r.release_id ?? "") ?? [];
  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
  };

  return (
    <div className="page">
      <header className="page-head">
        <h1>Публикации</h1>
      </header>
      <FarmBanner />
      <Card
        actions={
          <div className="pub-filters">
            <select value={release} onChange={(e) => setFilter("release", e.target.value)} aria-label="Ролик">
              <option value="">Все ролики</option>
              {releases.filter(Boolean).map((r) => (
                <option key={r} value={r}>
                  {shortId(r)}
                </option>
              ))}
            </select>
            <select value={account} onChange={(e) => setFilter("account", e.target.value)} aria-label="Аккаунт">
              <option value="">Все аккаунты</option>
              {data?.all_accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.id}
                </option>
              ))}
            </select>
            <div className="chips">
              {STATUSES.map((s) => (
                <button key={s || "all"} className={`chip${status === s ? " chip-on" : ""}`} onClick={() => setFilter("status", s)}>
                  {s ? JOB_STATUS_LABEL[s] : "Все"}
                </button>
              ))}
            </div>
          </div>
        }
      >
        <ErrorNote error={error} />
        {data && data.rows.length === 0 && (
          <Empty>
            {release || account || status ? "Ничего не найдено" : "Публикаций пока нет — отправьте одобренный ролик из раздела «Видео»"}
          </Empty>
        )}
        {data && data.rows.length > 0 && (
          <div className="table-wrap">
            <table className="table pub-grid">
              <thead>
                <tr>
                  <th>Ролик</th>
                  {data.accounts.map((a) => (
                    <th key={a.id}>
                      {a.platform && <span className="pub-platform">{PLATFORM_SHORT[a.platform]}</span>} {a.id}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row) => (
                  <tr key={row.release_id ?? "—"}>
                    <td>
                      {row.release_id ? <Link to={`/videos?open=${encodeURIComponent(row.release_id)}`}>{shortId(row.release_id)}</Link> : "без ролика"}
                      <div className="muted">{agoTs(row.updated_at)}</div>
                    </td>
                    {data.accounts.map((a) => (
                      <td key={a.id}>{row.cells[a.id] ? <Cell cell={row.cells[a.id]} onShot={setShot} /> : <span className="muted">—</span>}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {shot && (
        <div className="lightbox" onClick={() => setShot(null)}>
          <img src={shot} alt="Скриншот ошибки" />
        </div>
      )}
    </div>
  );
}
