import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { Card, Empty, ErrorNote } from "../components/ui";
import { advFileUrl } from "../ads";
import type { Advertiser } from "../types/ads";
import "../ads.css";

function NewAdvertiser() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [brand, setBrand] = useState("");
  const [legal, setLegal] = useState("");
  const [inn, setInn] = useState("");
  const create = useMutation({
    mutationFn: () => api.post<Advertiser>("/advertisers", { brand, legal_name: legal, inn }),
    onSuccess: (adv) => {
      qc.invalidateQueries({ queryKey: ["advertisers"] });
      nav(`/advertisers/${adv.id}`);
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };
  return (
    <Card title="Новый рекламодатель">
      <form className="form" onSubmit={submit}>
        <label>
          Бренд
          <input value={brand} onChange={(e) => setBrand(e.target.value)} placeholder="Быстрая Корочка" />
        </label>
        <label>
          Юрлицо
          <input value={legal} onChange={(e) => setLegal(e.target.value)} placeholder="ООО «Корочка»" />
        </label>
        <label>
          ИНН
          <input value={inn} inputMode="numeric" onChange={(e) => setInn(e.target.value)} placeholder="10 или 12 цифр" />
        </label>
        <div className="form-foot">
          <ErrorNote error={create.error} />
          <button className="btn btn-primary" disabled={!brand.trim() || create.isPending}>
            {create.isPending ? "Создаю…" : "Создать"}
          </button>
        </div>
      </form>
    </Card>
  );
}

export function AdvertisersPage() {
  const [archived, setArchived] = useState(false);
  const { data, error } = useQuery({
    queryKey: ["advertisers", archived],
    queryFn: () => api.get<Advertiser[]>(`/advertisers${archived ? "?archived=true" : ""}`),
  });
  return (
    <div className="page">
      <header className="page-head">
        <h1>Рекламодатели</h1>
      </header>
      <NewAdvertiser />
      <Card
        title="Список"
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
        {data && data.length === 0 && <Empty>Рекламодателей пока нет</Empty>}
        {data && data.length > 0 && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th />
                  <th>Бренд</th>
                  <th className="hide-mobile">Юрлицо</th>
                  <th className="hide-mobile">ИНН</th>
                  <th>Файлы</th>
                </tr>
              </thead>
              <tbody>
                {data.map((a) => (
                  <tr key={a.id}>
                    <td>
                      {a.logo ? <img className="ads-logo" src={advFileUrl(a, a.logo, 320)} alt="" loading="lazy" /> : <span className="ads-logo" />}
                    </td>
                    <td>
                      <Link to={`/advertisers/${a.id}`}>{a.brand}</Link>
                      {a.archived && <div className="muted">в архиве</div>}
                    </td>
                    <td className="hide-mobile">{a.legal_name || <span className="ads-warn">не указано</span>}</td>
                    <td className="hide-mobile muted nowrap">{a.inn || "—"}</td>
                    <td className="muted nowrap">
                      {a.logo ? "лого" : <span className="ads-warn">нет лого</span>} · фото: {a.products.length}
                    </td>
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
