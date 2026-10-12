import { useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type BriefRow, type BriefState } from "../api";
import { Card, Empty, ErrorNote, Progress, StateBadge } from "../components/ui";
import { CharacterPicker } from "../components/CharacterPicker";
import type { Campaign } from "../types/ads";
import type { Character } from "../types/characters";
import type { World } from "../types/worlds";
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
  const [params] = useSearchParams();
  const [text, setText] = useState("");
  const [characters, setCharacters] = useState<string[]>(() =>
    (params.get("characters") ?? "").split(",").map((s) => s.trim()).filter(Boolean),
  );
  const [campaign, setCampaign] = useState(() => params.get("campaign") ?? "");
  const campaigns = useQuery({ queryKey: ["campaigns"], queryFn: () => api.get<Campaign[]>("/campaigns") });
  const activeCampaigns = (campaigns.data ?? []).filter((c) => c.status === "active" || c.id === campaign);
  const [world, setWorld] = useState(() => params.get("world") ?? "");
  const worlds = useQuery({ queryKey: ["worlds", "0"], queryFn: () => api.get<World[]>("/worlds?archived=0") });
  const pickWorld = async (id: string) => {
    setWorld(id);
    if (!id) return;
    setCampaign(""); // world episodes cannot carry advertising
    try {
      const chars = await qc.fetchQuery({
        queryKey: ["characters", "world", id],
        queryFn: () => api.get<Character[]>(`/characters?world=${encodeURIComponent(id)}`),
      });
      const ready = chars.filter((c) => c.turnaround_path && !c.library.archived).map((c) => c.id);
      if (ready.length) setCharacters(ready);
    } catch {
      /* preselect is best-effort */
    }
  };
  const [bait, setBait] = useState("none");
  const [product, setProduct] = useState("");
  const [duration, setDuration] = useState("");
  const [audience, setAudience] = useState("");
  const create = useMutation({
    mutationFn: () =>
      api.post<{ id: string }>("/briefs", {
        text,
        options: {
          comment_bait: bait,
          product,
          duration,
          audience,
          ...(characters.length ? { characters: characters.join(",") } : {}),
          ...(world ? { world } : campaign ? { campaign } : {}),
        },
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
        <label>
          Мир сериала
          <select value={world} onChange={(e) => pickWorld(e.target.value)}>
            <option value="">Без мира</option>
            {(worlds.data ?? []).map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
            {world && worlds.data && !worlds.data.some((w) => w.id === world) && <option value={world}>{world}</option>}
          </select>
        </label>
        <label title={world ? "Эпизод мира не может быть рекламой" : undefined}>
          Рекламная кампания
          <select value={world ? "" : campaign} onChange={(e) => setCampaign(e.target.value)} disabled={!!world}>
            <option value="">Без рекламы</option>
            {activeCampaigns.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
                {c.advertiser_brand ? ` · ${c.advertiser_brand}` : ""}
              </option>
            ))}
          </select>
        </label>
        <div className="span-2">
          <span className="field-label">Персонажи из библиотеки</span>
          <CharacterPicker value={characters} onChange={setCharacters} />
        </div>
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
