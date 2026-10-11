import { useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type BriefRow, type BriefState } from "../api";
import { Card, Empty, ErrorNote, Progress, StateBadge } from "../components/ui";
import { ago, STAGE_LABEL, STATE_LABEL, shortId } from "../format";

const FILTERS: (BriefState | "")[] = ["", "queued", "running", "awaiting_approval", "failed", "budget", "approved", "rejected"];
const BAIT: { value: string; label: string }[] = [
  { value: "none", label: "Без механики" },
  { value: "hero_breaks_wall", label: "Герой ломает четвёртую стену" },
  { value: "meta_joke", label: "Мета-шутка" },
];

function NewBrief() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [text, setText] = useState("");
  const [bait, setBait] = useState("none");
  const [product, setProduct] = useState("");
  const [duration, setDuration] = useState("");
  const [audience, setAudience] = useState("");
  const create = useMutation({
    mutationFn: () =>
      api.post<{ id: string }>("/briefs", {
        text,
        options: { comment_bait: bait, product, duration, audience },
      }),
    onSuccess: ({ id }) => {
      qc.invalidateQueries({ queryKey: ["briefs"] });
      qc.invalidateQueries({ queryKey: ["overview"] });
      nav(`/briefs/${id}`);
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };

  return (
    <Card title="Новый бриф">
      <form className="form" onSubmit={submit}>
        <label className="span-2">
          Что снять
          <textarea
            rows={4}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Кот-повар готовит пиццу и рекламирует доставку «Быстрая Корочка». 30 секунд, говорит по-русски."
          />
        </label>
        <label>
          Продукт
          <input value={product} onChange={(e) => setProduct(e.target.value)} placeholder="Доставка «Быстрая Корочка»" />
        </label>
        <label>
          Длительность
          <input value={duration} onChange={(e) => setDuration(e.target.value)} placeholder="30 секунд" />
        </label>
        <label>
          Аудитория
          <input value={audience} onChange={(e) => setAudience(e.target.value)} placeholder="Семьи, 25–45" />
        </label>
        <label>
          Механика комментариев
          <select value={bait} onChange={(e) => setBait(e.target.value)}>
            {BAIT.map((b) => (
              <option key={b.value} value={b.value}>
                {b.label}
              </option>
            ))}
          </select>
        </label>
        <div className="span-2 form-foot">
          <ErrorNote error={create.error} />
          <button className="btn btn-primary" disabled={text.trim().length < 10 || create.isPending}>
            {create.isPending ? "Ставлю…" : "Поставить в очередь"}
          </button>
        </div>
      </form>
    </Card>
  );
}

export function BriefsPage() {
  const [params, setParams] = useSearchParams();
  const state = params.get("state") ?? "";
  const { data, error } = useQuery({
    queryKey: ["briefs", state],
    queryFn: () => api.get<BriefRow[]>(`/briefs${state ? `?state=${state}` : ""}`),
  });

  return (
    <div className="page">
      <header className="page-head">
        <h1>Производство</h1>
      </header>
      <NewBrief />
      <Card
        title="Брифы"
        actions={
          <div className="chips">
            {FILTERS.map((f) => (
              <button
                key={f || "all"}
                className={`chip${state === f ? " chip-on" : ""}`}
                onClick={() => setParams(f ? { state: f } : {})}
              >
                {f ? STATE_LABEL[f] : "Все"}
              </button>
            ))}
          </div>
        }
      >
        <ErrorNote error={error} />
        {data && data.length === 0 && <Empty>Нет брифов</Empty>}
        {data && data.length > 0 && (
          <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Бриф</th>
                <th>Состояние</th>
                <th className="hide-mobile">Этапы</th>
                <th className="hide-mobile">Кредиты</th>
                <th>Обновлён</th>
              </tr>
            </thead>
            <tbody>
              {data.map((b) => (
                <tr key={b.id}>
                  <td>
                    <Link to={`/briefs/${b.id}`}>{shortId(b.id)}</Link>
                    <div className="muted clamp">{b.text}</div>
                  </td>
                  <td>
                    <StateBadge state={b.state} />
                    {b.current_stage && <div className="muted">{STAGE_LABEL[b.current_stage]}</div>}
                  </td>
                  <td className="cell-progress hide-mobile">
                    <Progress done={b.stages_done} total={b.stages_total} active={b.state === "running"} />
                  </td>
                  <td className="muted nowrap hide-mobile">
                    {b.credits.spent !== undefined ? `${b.credits.spent} / ${b.credits.budget}` : "—"}
                  </td>
                  <td className="muted nowrap">{ago(b.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </Card>
    </div>
  );
}
