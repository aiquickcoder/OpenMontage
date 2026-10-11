import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { Card, Empty, ErrorNote } from "../components/ui";
import { FarmBanner } from "../components/FarmBanner";
import { MODE_LABEL, PLATFORM_LABEL, commandNote, splitList, useFarmRefresh } from "../farm";
import type { Account, AccountInput, AccountsResponse, CommandResult, Platform } from "../types/farm";
import "../farm.css";

const HOURS_RE = /^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$/;

interface Draft {
  id: string;
  platform: Platform;
  username: string;
  device: string;
  started_at: string;
  mode: "warmup" | "audience";
  sessions: string;
  hours: string;
  allow_follows: boolean;
  hashtags: string;
  bloggers: string;
}

const today = () => new Date().toISOString().slice(0, 10);

function toDraft(a: Account | null, devices: string[]): Draft {
  return {
    id: a?.id ?? "",
    platform: a?.platform ?? "instagram",
    username: a?.username ?? "",
    device: a?.device ?? devices[0] ?? "",
    started_at: a?.started_at ? String(a.started_at).slice(0, 10) : today(),
    mode: a?.mode ?? "warmup",
    sessions: a?.sessions_per_day !== undefined ? String(a.sessions_per_day) : "2-3",
    hours: (a?.working_hours ?? ["10:00-13:00", "18:00-22:30"]).join(", "),
    allow_follows: a?.allow_follows ?? false,
    hashtags: (a?.hashtags ?? []).join(" "),
    bloggers: (a?.bloggers ?? []).join(" "),
  };
}

/** Same rules as studio/farm.py; the server re-checks everything. */
function validate(d: Draft, others: Account[], creating: boolean): Partial<Record<keyof Draft, string>> {
  const e: Partial<Record<keyof Draft, string>> = {};
  if (creating) {
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(d.id)) e.id = "Латиница, цифры, «_», «-», например ig_fox";
    else if (others.some((a) => a.id === d.id)) e.id = "Такой id уже есть";
  }
  if (!/^@?[A-Za-z0-9._-]{1,64}$/.test(d.username.trim())) e.username = "Логин без пробелов, латиница";
  if (!d.device) e.device = "Выберите телефон";
  else if (others.some((a) => a.device === d.device && a.platform === d.platform && a.id !== d.id))
    e.device = `На ${d.device} уже есть аккаунт ${PLATFORM_LABEL[d.platform]} — один на платформу`;
  const m = d.sessions.trim().match(/^(\d{1,2})(?:\s*-\s*(\d{1,2}))?$/);
  if (!m) e.sessions = "Число 0–20 или диапазон «2-3»";
  else {
    const lo = Number(m[1]);
    const hi = Number(m[2] ?? m[1]);
    if (lo > hi || hi > 20) e.sessions = "От 0 до 20, начало не больше конца";
  }
  const hours = splitList(d.hours);
  if (hours.length === 0 || hours.length > 6) e.hours = "От 1 до 6 интервалов";
  else {
    const bad = hours.find((h) => !HOURS_RE.test(h) || h.split("-")[0] >= h.split("-")[1]);
    if (bad) e.hours = `«${bad}»: нужен вид ЧЧ:ММ-ЧЧ:ММ, начало раньше конца`;
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(d.started_at)) e.started_at = "Дата ГГГГ-ММ-ДД";
  return e;
}

function toInput(d: Draft, creating: boolean): AccountInput {
  const s = d.sessions.trim().replace(/\s/g, "");
  const body: AccountInput = {
    platform: d.platform,
    username: d.username.trim().replace(/^@/, ""),
    device: d.device,
    started_at: d.started_at,
    mode: d.mode,
    sessions_per_day: /^\d+$/.test(s) ? Number(s) : s,
    working_hours: splitList(d.hours),
    allow_follows: d.allow_follows,
    hashtags: splitList(d.hashtags),
    bloggers: splitList(d.bloggers),
  };
  if (creating) body.id = d.id.trim();
  return body;
}

function AccountDrawer({
  account,
  data,
  onClose,
}: {
  account: Account | null; // null = new
  data: AccountsResponse;
  onClose: () => void;
}) {
  const refresh = useFarmRefresh();
  const creating = account === null;
  const [d, setD] = useState<Draft>(() => toDraft(account, data.devices));
  const [touched, setTouched] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const errors = validate(d, data.accounts, creating);
  const invalid = Object.keys(errors).length > 0;
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setD((prev) => ({ ...prev, [k]: v }));
  const save = useMutation({
    mutationFn: () =>
      creating
        ? api.post<CommandResult>("/accounts", toInput(d, true))
        : api.patch<CommandResult>(`/accounts/${encodeURIComponent(account.id)}`, toInput(d, false)),
    onSuccess: (res) => {
      refresh();
      if (res.status === "done") onClose();
      else setNote(commandNote(res));
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (!invalid) save.mutate();
  };
  const err = (k: keyof Draft) => touched && errors[k] && <span className="field-error">{errors[k]}</span>;

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()}>
        <header className="drawer-head">
          <div>
            <h2>{creating ? "Новый аккаунт" : account.id}</h2>
            <span className="muted">Изменения уходят на ферму одной правкой accounts.yaml (с резервной копией)</span>
          </div>
          <button className="btn btn-ghost" onClick={onClose} aria-label="Закрыть">
            ✕
          </button>
        </header>
        <form className="drawer-body form" onSubmit={submit}>
          {creating && (
            <label className="span-2">
              id
              <input value={d.id} onChange={(e) => set("id", e.target.value)} placeholder="ig_fox" autoFocus />
              {err("id")}
            </label>
          )}
          <label>
            Платформа
            <select value={d.platform} onChange={(e) => set("platform", e.target.value as Platform)}>
              {data.platforms.map((p) => (
                <option key={p} value={p}>
                  {PLATFORM_LABEL[p] ?? p}
                </option>
              ))}
            </select>
          </label>
          <label>
            Логин
            <input value={d.username} onChange={(e) => set("username", e.target.value)} placeholder="fox_cartoons" />
            {err("username")}
          </label>
          <label>
            Телефон
            <select value={d.device} onChange={(e) => set("device", e.target.value)}>
              {data.devices.length === 0 && <option value="">нет устройств</option>}
              {data.devices.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
            {err("device")}
          </label>
          <label>
            Режим
            <select value={d.mode} onChange={(e) => set("mode", e.target.value as Draft["mode"])}>
              <option value="warmup">Прогрев</option>
              <option value="audience">Аудитория</option>
            </select>
          </label>
          <label>
            Старт автоматизации
            <input type="date" value={d.started_at} onChange={(e) => set("started_at", e.target.value)} />
            {err("started_at")}
          </label>
          <label>
            Сеансов в день
            <input value={d.sessions} onChange={(e) => set("sessions", e.target.value)} placeholder="2-3" />
            {err("sessions")}
          </label>
          <label className="span-2">
            Рабочие часы
            <input value={d.hours} onChange={(e) => set("hours", e.target.value)} placeholder="10:00-13:00, 18:00-22:30" />
            {err("hours")}
          </label>
          {d.mode === "audience" && (
            <>
              <label className="span-2">
                Хэштеги (источники аудитории)
                <input value={d.hashtags} onChange={(e) => set("hashtags", e.target.value)} placeholder="мультики анимация" />
              </label>
              <label className="span-2">
                Блогеры-доноры
                <input value={d.bloggers} onChange={(e) => set("bloggers", e.target.value)} placeholder="some_competitor" />
              </label>
            </>
          )}
          <label className="farm-check span-2">
            <input type="checkbox" checked={d.allow_follows} onChange={(e) => set("allow_follows", e.target.checked)} />
            <span>Разрешить подписки</span>
          </label>
          <div className="span-2">
            <ErrorNote error={save.error} />
            {note && <div className="farm-note">{note}</div>}
          </div>
          <div className="span-2 form-foot">
            <button className="btn btn-primary" disabled={save.isPending || (touched && invalid)}>
              {save.isPending ? "Сохраняю…" : creating ? "Добавить" : "Сохранить"}
            </button>
          </div>
        </form>
      </aside>
    </div>
  );
}

function DeleteButton({ account }: { account: Account }) {
  const refresh = useFarmRefresh();
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const t = window.setTimeout(() => setArmed(false), 4000);
    return () => window.clearTimeout(t);
  }, [armed]);
  const del = useMutation({
    mutationFn: () => api.del<CommandResult>(`/accounts/${encodeURIComponent(account.id)}`),
    onSuccess: refresh,
    onSettled: () => setArmed(false),
  });
  return (
    <>
      <button
        className={`btn ${armed ? "btn-danger farm-armed" : "btn-ghost"}`}
        disabled={del.isPending}
        onClick={() => (armed ? del.mutate() : setArmed(true))}
      >
        {del.isPending ? "Удаляю…" : armed ? "Точно удалить?" : "Удалить"}
      </button>
      {del.error && <ErrorNote error={del.error} />}
    </>
  );
}

export function AccountsPage() {
  const { data, error } = useQuery({
    queryKey: ["accounts"],
    queryFn: () => api.get<AccountsResponse>("/accounts"),
    refetchInterval: 20_000,
  });
  const [open, setOpen] = useState<string | null>(null); // account id, "new", or null
  const current = open && open !== "new" ? data?.accounts.find((a) => a.id === open) ?? null : null;

  return (
    <div className="page">
      <header className="page-head">
        <h1>Аккаунты</h1>
        <div className="head-actions">
          <button className="btn btn-primary" disabled={!data || data.devices.length === 0} onClick={() => setOpen("new")}>
            + Аккаунт
          </button>
        </div>
      </header>
      <FarmBanner />
      <ErrorNote error={error} />
      {data?.pending && <div className="farm-note">Есть правка, которую ферма ещё не применила — список показан с ней.</div>}
      <Card>
        {data && data.accounts.length === 0 && (
          <Empty>{data.devices.length ? "Аккаунтов пока нет" : "Сначала подключите ферму — без телефонов аккаунт некуда привязать"}</Empty>
        )}
        {data && data.accounts.length > 0 && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Аккаунт</th>
                  <th>Телефон</th>
                  <th>Режим</th>
                  <th className="hide-mobile">Сеансы</th>
                  <th className="hide-mobile">Часы</th>
                  <th className="hide-mobile">Старт</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.accounts.map((a) => (
                  <tr key={a.id}>
                    <td>
                      <a href="#" onClick={(e) => (e.preventDefault(), setOpen(a.id))}>
                        {a.id}
                      </a>
                      <div className="muted">
                        {PLATFORM_LABEL[a.platform] ?? a.platform} · @{a.username}
                      </div>
                    </td>
                    <td>{a.device}</td>
                    <td>{MODE_LABEL[a.mode ?? "warmup"] ?? a.mode}</td>
                    <td className="hide-mobile">{String(a.sessions_per_day ?? "—")}</td>
                    <td className="hide-mobile muted">{(a.working_hours ?? []).join(", ")}</td>
                    <td className="hide-mobile muted nowrap">{a.started_at ? String(a.started_at).slice(0, 10) : "—"}</td>
                    <td className="nowrap">
                      <button className="btn btn-ghost" onClick={() => setOpen(a.id)}>
                        Изменить
                      </button>
                      <DeleteButton account={a} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {data && open && (open === "new" || current) && (
        <AccountDrawer key={open} account={current} data={data} onClose={() => setOpen(null)} />
      )}
    </div>
  );
}
