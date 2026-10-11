import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { ErrorNote } from "./ui";
import { PLATFORM_LABEL, commandNote, useFarmRefresh, useFarmStatus } from "../farm";
import type { AccountsResponse, CommandResult, PublishInput } from "../types/farm";
import "../farm.css";

/** Send an approved video to the farm: pick accounts, a start time and how far apart to post. */
export function PublishDialog({ briefId, onClose }: { briefId: string; onClose: () => void }) {
  const refresh = useFarmRefresh();
  const status = useFarmStatus();
  const { data, error } = useQuery({ queryKey: ["accounts"], queryFn: () => api.get<AccountsResponse>("/accounts") });
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [when, setWhen] = useState(""); // datetime-local; empty = now
  const [spread, setSpread] = useState(30);
  const [note, setNote] = useState<string | null>(null);
  const send = useMutation({
    mutationFn: () => {
      const body: PublishInput = {
        accounts: [...picked],
        start_at: when ? new Date(when).toISOString() : null,
        spread_minutes: spread,
      };
      return api.post<CommandResult>(`/videos/${encodeURIComponent(briefId)}/publish`, body);
    },
    onSuccess: (res) => {
      refresh();
      setNote(commandNote(res, "Публикации поставлены в очередь фермы"));
    },
  });
  const toggle = (id: string) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const spreadBad = !Number.isInteger(spread) || spread < 0 || spread > 1440;

  return (
    <div className="farm-modal-backdrop" onClick={onClose}>
      <div className="farm-modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Публикация">
        <header className="drawer-head">
          <div>
            <h2>Опубликовать через ферму</h2>
            <span className="muted">Телефоны выложат ролик с выбранных аккаунтов</span>
          </div>
          <button className="btn btn-ghost" onClick={onClose} aria-label="Закрыть">
            ✕
          </button>
        </header>
        <div className="drawer-body">
          {status.data && !status.data.online && (
            <div className="farm-banner farm-banner-bad">
              <strong>Ферма офлайн — команды выполнятся при подключении</strong>
            </div>
          )}
          <ErrorNote error={error} />
          <fieldset className="farm-accounts">
            <legend>Аккаунты</legend>
            {data && data.accounts.length === 0 && <span className="muted">Нет аккаунтов — добавьте их в разделе «Аккаунты»</span>}
            {data?.accounts.map((a) => (
              <label key={a.id} className="farm-check">
                <input type="checkbox" checked={picked.has(a.id)} onChange={() => toggle(a.id)} />
                <span>
                  {PLATFORM_LABEL[a.platform] ?? a.platform} · @{a.username} <span className="muted">({a.device})</span>
                </span>
              </label>
            ))}
          </fieldset>
          <div className="form">
            <label>
              Начать
              <input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
              <span className="muted">{when ? "" : "пусто — сразу"}</span>
            </label>
            <label>
              Разнести на, минут
              <input type="number" min={0} max={1440} step={5} value={spread} onChange={(e) => setSpread(Number(e.target.value))} />
              {spreadBad && <span className="field-error">От 0 до 1440</span>}
            </label>
          </div>
          <ErrorNote error={send.error} />
          {note && <div className="farm-note">{note}</div>}
          <div className="drawer-actions">
            <button
              className="btn btn-primary"
              disabled={picked.size === 0 || spreadBad || send.isPending || send.isSuccess}
              onClick={() => send.mutate()}
            >
              {send.isPending ? "Отправляю…" : `Опубликовать (${picked.size})`}
            </button>
            <button className="btn btn-ghost" onClick={onClose}>
              {send.isSuccess ? "Готово" : "Отмена"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
