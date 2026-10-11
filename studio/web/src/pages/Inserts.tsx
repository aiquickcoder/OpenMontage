import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { Card, Empty, ErrorNote } from "../components/ui";
import { PLACEMENT_LABEL } from "../ads";
import type { Insert, Placement } from "../types/ads";
import "../ads.css";

export const DEFAULT_TEMPLATE = [
  "Встрой {product} бренда {brand} нативно: герой сам пользуется продуктом в ключевой сцене.",
  "Призыв в конце: «{cta}». Ссылка: {link}, промокод {promo_code}.",
  "Обязательно: {must}.",
  "Нельзя: {must_not}.",
].join("\n");

function NewInsert() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [name, setName] = useState("");
  const [placement, setPlacement] = useState<Placement>("hero_uses_product");
  const create = useMutation({
    mutationFn: () =>
      api.post<Insert>("/inserts", { name, placement, max_screen_share: 0.2, template: DEFAULT_TEMPLATE }),
    onSuccess: (ins) => {
      qc.invalidateQueries({ queryKey: ["inserts"] });
      nav(`/inserts/${ins.id}`);
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };
  return (
    <Card title="Новый шаблон вставки">
      <form className="form" onSubmit={submit}>
        <label>
          Название
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Нативная, герой пробует" />
        </label>
        <label>
          Размещение
          <select value={placement} onChange={(e) => setPlacement(e.target.value as Placement)}>
            {Object.entries(PLACEMENT_LABEL).map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <div className="span-2 form-foot">
          <ErrorNote error={create.error} />
          <button className="btn btn-primary" disabled={!name.trim() || create.isPending}>
            {create.isPending ? "Создаю…" : "Создать и редактировать"}
          </button>
        </div>
      </form>
    </Card>
  );
}

export function InsertsPage() {
  const [archived, setArchived] = useState(false);
  const { data, error } = useQuery({
    queryKey: ["inserts", archived],
    queryFn: () => api.get<Insert[]>(`/inserts${archived ? "?archived=true" : ""}`),
  });
  return (
    <div className="page">
      <header className="page-head">
        <h1>Шаблоны вставок</h1>
      </header>
      <NewInsert />
      <Card
        title="Шаблоны"
        actions={
          <div className="chips">
            <button className={`chip${!archived ? " chip-on" : ""}`} onClick={() => setArchived(false)}>
              Активные
            </button>
            <button className={`chip${archived ? " chip-on" : ""}`} onClick={() => setArchived(true)}>
              Все, включая архив
            </button>
          </div>
        }
      >
        <ErrorNote error={error} />
        {data && data.length === 0 && <Empty>Шаблонов пока нет</Empty>}
        {data && data.length > 0 && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Шаблон</th>
                  <th>Размещение</th>
                  <th className="hide-mobile">Доля экрана</th>
                  <th className="hide-mobile">Версия</th>
                </tr>
              </thead>
              <tbody>
                {data.map((i) => (
                  <tr key={i.id}>
                    <td>
                      <Link to={`/inserts/${i.id}`}>{i.name}</Link>
                      <div className="muted clamp">{i.template}</div>
                      {i.archived && <div className="muted">в архиве</div>}
                    </td>
                    <td>{PLACEMENT_LABEL[i.placement]}</td>
                    <td className="hide-mobile nowrap">до {Math.round(i.max_screen_share * 100)}%</td>
                    <td className="hide-mobile muted">v{i.version}</td>
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
