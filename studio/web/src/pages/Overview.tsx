import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, type BriefState, type Overview } from "../api";
import { Card, Empty, ErrorNote, Progress, StateBadge } from "../components/ui";
import { ago, STAGE_LABEL, STATE_LABEL, shortId } from "../format";

const TILES: BriefState[] = ["queued", "running", "awaiting_approval", "approved", "failed", "budget"];
const SERVICE_LABEL: Record<string, string> = {
  "factory-worker": "Конвейер",
  "factory-bot": "Telegram-бот",
  "factory-studio": "Studio",
};

export function OverviewPage() {
  const { data, error } = useQuery({
    queryKey: ["overview"],
    queryFn: () => api.get<Overview>("/overview"),
    refetchInterval: 30_000,
  });
  if (error) return <ErrorNote error={error} />;
  if (!data) return <div className="loading">Загрузка…</div>;

  return (
    <div className="page">
      <header className="page-head">
        <h1>Обзор</h1>
        <div className="services">
          {Object.entries(data.services).map(([name, st]) => (
            <span key={name} className={`service service-${st === "active" ? "ok" : "bad"}`}>
              {SERVICE_LABEL[name] ?? name}: {st === "active" ? "работает" : st}
            </span>
          ))}
        </div>
      </header>

      <div className="tiles">
        {TILES.map((st) => (
          <Link key={st} to={st === "awaiting_approval" ? "/videos" : `/briefs?state=${st}`} className={`tile tile-${st}`}>
            <span className="tile-num">{data.counts[st] ?? 0}</span>
            <span className="tile-label">{STATE_LABEL[st]}</span>
          </Link>
        ))}
      </div>

      <Card title="Сейчас делается">
        {data.running.length === 0 ? (
          <Empty>Конвейер свободен. <Link to="/briefs">Новый бриф →</Link></Empty>
        ) : (
          data.running.map((b) => (
            <Link key={b.id} to={`/briefs/${b.id}`} className="running-row">
              <div>
                <strong>{shortId(b.id)}</strong>
                <div className="muted">
                  {b.current_stage ? `Этап: ${STAGE_LABEL[b.current_stage] ?? b.current_stage}` : "Запуск"} · начат{" "}
                  {ago(b.updated_at)}
                </div>
              </div>
              <div className="running-progress">
                <Progress done={b.stages_done} total={b.stages_total} active />
                <span className="muted">
                  {b.stages_done}/{b.stages_total}
                  {b.credits.spent !== undefined && ` · ${b.credits.spent}/${b.credits.budget} кр.`}
                </span>
              </div>
            </Link>
          ))
        )}
      </Card>

      <Card title="Последние брифы" actions={<Link to="/briefs">Все →</Link>}>
        <div className="table-wrap">
        <table className="table">
          <tbody>
            {data.recent.map((b) => (
              <tr key={b.id}>
                <td>
                  <Link to={`/briefs/${b.id}`}>{shortId(b.id)}</Link>
                  <div className="muted clamp">{b.text}</div>
                </td>
                <td>
                  <StateBadge state={b.state} />
                </td>
                <td className="muted nowrap">{ago(b.updated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      </Card>
    </div>
  );
}
