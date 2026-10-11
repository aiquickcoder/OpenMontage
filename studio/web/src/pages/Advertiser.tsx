import { useEffect, useState, type ChangeEvent, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { Card, Empty, ErrorNote } from "../components/ui";
import { advFileUrl, CAMPAIGN_STATUS_LABEL, lines, uploadAdvertiserFile } from "../ads";
import type { Advertiser, AdvertiserInput, Campaign } from "../types/ads";
import "../ads.css";

type Draft = Omit<Required<AdvertiserInput>, "must_say" | "must_not_say" | "archived"> & { must_say: string; must_not_say: string };

const toDraft = (a: Advertiser): Draft => ({
  brand: a.brand,
  legal_name: a.legal_name,
  inn: a.inn,
  link: a.link,
  promo_code: a.promo_code,
  cta: a.cta,
  must_say: a.must_say.join("\n"),
  must_not_say: a.must_not_say.join("\n"),
  notes: a.notes,
});

function Files({ adv }: { adv: Advertiser }) {
  const qc = useQueryClient();
  const done = (fresh: Advertiser) => {
    qc.setQueryData(["advertiser", adv.id], fresh);
    qc.invalidateQueries({ queryKey: ["advertisers"] });
  };
  const upload = useMutation({
    mutationFn: async ({ kind, files }: { kind: "logo" | "product"; files: File[] }) => {
      let last = adv;
      for (const f of files) last = await uploadAdvertiserFile(adv.id, kind, f);
      return last;
    },
    onSuccess: done,
  });
  const remove = useMutation({
    mutationFn: (rel: string) => api.del<Advertiser>(`/advertisers/${encodeURIComponent(adv.id)}/file/${rel}`),
    onSuccess: done,
  });
  const pick = (kind: "logo" | "product") => (e: ChangeEvent<HTMLInputElement>) => {
    const files = [...(e.target.files ?? [])];
    e.target.value = "";
    if (files.length) upload.mutate({ kind, files });
  };

  return (
    <Card title="Логотип и фото продукта">
      <div className="ads-files">
        <div>
          {adv.logo ? (
            <img className="ads-logo-lg" src={advFileUrl(adv, adv.logo, 320)} alt="Логотип" />
          ) : (
            <div className="ads-logo-lg">нет логотипа</div>
          )}
          <label className="btn ads-upload">
            {adv.logo ? "Заменить лого" : "Загрузить лого"}
            <input type="file" accept="image/png,image/jpeg,image/webp" onChange={pick("logo")} />
          </label>
        </div>
        <div className="gallery">
          {adv.products.map((rel) => (
            <div key={rel} className="thumb ads-thumb">
              <a href={advFileUrl(adv, rel)} target="_blank" rel="noreferrer">
                <img src={advFileUrl(adv, rel, 320)} alt="" loading="lazy" />
              </a>
              <span>{rel.replace("products/", "")}</span>
              <button
                className="ads-del"
                aria-label="Удалить фото"
                disabled={remove.isPending}
                onClick={() => confirm("Удалить фото?") && remove.mutate(rel)}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      </div>
      <p className="muted">PNG, JPG или WEBP до 15 МБ. Нужны 3–6 фото продукта с разных сторон на чистом фоне.</p>
      <label className="btn ads-upload">
        {upload.isPending ? "Загружаю…" : "Добавить фото продукта"}
        <input type="file" accept="image/png,image/jpeg,image/webp" multiple onChange={pick("product")} />
      </label>
      <ErrorNote error={upload.error || remove.error} />
    </Card>
  );
}

export function AdvertiserPage() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const { data: adv, error } = useQuery({
    queryKey: ["advertiser", id],
    queryFn: () => api.get<Advertiser>(`/advertisers/${encodeURIComponent(id)}`),
  });
  const campaigns = useQuery({ queryKey: ["campaigns"], queryFn: () => api.get<Campaign[]>("/campaigns") });
  const [draft, setDraft] = useState<Draft | null>(null);
  useEffect(() => {
    if (adv) setDraft(toDraft(adv));
  }, [adv]);

  const refresh = (fresh: Advertiser) => {
    qc.setQueryData(["advertiser", id], fresh);
    qc.invalidateQueries({ queryKey: ["advertisers"] });
  };
  const save = useMutation({
    mutationFn: (d: Draft) =>
      api.patch<Advertiser>(`/advertisers/${encodeURIComponent(id)}`, {
        ...d,
        must_say: lines(d.must_say),
        must_not_say: lines(d.must_not_say),
      }),
    onSuccess: refresh,
  });
  const archive = useMutation({
    mutationFn: (archived: boolean) => api.patch<Advertiser>(`/advertisers/${encodeURIComponent(id)}`, { archived }),
    onSuccess: refresh,
  });

  if (error) return <div className="page"><ErrorNote error={error} /></div>;
  if (!adv || !draft) return <div className="page muted">Загрузка…</div>;
  const set = (k: keyof Draft) => (e: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setDraft({ ...draft, [k]: e.target.value });
  const dirty = JSON.stringify(draft) !== JSON.stringify(toDraft(adv));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate(draft);
  };
  const mine = (campaigns.data ?? []).filter((c) => c.advertiser_id === adv.id);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <Link to="/advertisers" className="muted">← Рекламодатели</Link>
          <h1>{adv.brand}</h1>
          {adv.archived && <span className="muted">в архиве</span>}
        </div>
        <button className="btn btn-ghost" disabled={archive.isPending} onClick={() => archive.mutate(!adv.archived)}>
          {adv.archived ? "Вернуть из архива" : "В архив"}
        </button>
      </header>

      <Card title="Данные">
        <form className="form" onSubmit={submit}>
          <label>
            Бренд
            <input value={draft.brand} onChange={set("brand")} />
          </label>
          <label>
            Юрлицо (для маркировки)
            <input value={draft.legal_name} onChange={set("legal_name")} placeholder="ООО «Ромашка»" />
          </label>
          <label>
            ИНН
            <input value={draft.inn} inputMode="numeric" onChange={set("inn")} placeholder="10 или 12 цифр" />
          </label>
          <label>
            Ссылка
            <input value={draft.link} onChange={set("link")} placeholder="https://…" />
          </label>
          <label>
            Призыв к действию (CTA)
            <input value={draft.cta} onChange={set("cta")} placeholder="Закажи в приложении" />
          </label>
          <label>
            Промокод
            <input value={draft.promo_code} onChange={set("promo_code")} />
          </label>
          <label>
            Обязательно сказать (по строке)
            <textarea rows={4} value={draft.must_say} onChange={set("must_say")} placeholder="Доставка за 30 минут" />
          </label>
          <label>
            Нельзя говорить (по строке)
            <textarea rows={4} value={draft.must_not_say} onChange={set("must_not_say")} placeholder={"лучший в мире\nобещания цены"} />
          </label>
          <label className="span-2">
            Заметки
            <textarea rows={3} value={draft.notes} onChange={set("notes")} />
          </label>
          <div className="span-2 form-foot">
            <ErrorNote error={save.error || archive.error} />
            <button className="btn btn-primary" disabled={!dirty || !draft.brand.trim() || save.isPending}>
              {save.isPending ? "Сохраняю…" : "Сохранить"}
            </button>
          </div>
        </form>
      </Card>

      <Files adv={adv} />

      <Card title="Кампании" actions={<Link className="btn btn-ghost" to="/campaigns">Все кампании</Link>}>
        {mine.length === 0 ? (
          <Empty>Кампаний пока нет</Empty>
        ) : (
          <ul className="events">
            {mine.map((c) => (
              <li key={c.id}>
                <Link to={`/campaigns/${c.id}`}>{c.name}</Link> <span className="muted">· {CAMPAIGN_STATUS_LABEL[c.status]}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
