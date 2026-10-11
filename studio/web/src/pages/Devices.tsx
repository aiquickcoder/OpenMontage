import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { Card, Empty, ErrorNote } from "../components/ui";
import { FarmBanner } from "../components/FarmBanner";
import { ADB_LABEL, MODE_LABEL, PLATFORM_SHORT, agoTs, commandNote, timeTs, useFarmRefresh, useFarmStatus } from "../farm";
import type { CommandResult, Device } from "../types/farm";
import "../farm.css";

type Info = Record<string, unknown>;

const scalar = (v: unknown) => (typeof v === "string" || typeof v === "number" || typeof v === "boolean" ? v : null);

/** The agent's `info` is free-form; pull the usual suspects out, show the rest as-is. */
function infoFacts(info: Info): { label: string; value: string; bad?: boolean }[] {
  const facts: { label: string; value: string; bad?: boolean }[] = [];
  const battery = scalar(info.battery) ?? scalar((info.battery as Info | undefined)?.level);
  if (battery !== null) facts.push({ label: "Батарея", value: `${battery}%`, bad: Number(battery) < 20 });
  const storage = scalar(info.storage_free) ?? scalar(info.storage) ?? scalar(info.free_storage);
  if (storage !== null) facts.push({ label: "Память", value: String(storage) });
  const vpn = info.vpn ?? info.vpn_active;
  if (vpn !== undefined)
    facts.push({ label: "VPN", value: vpn === true ? "включён" : vpn === false ? "выключен" : String(vpn), bad: vpn === false });
  const known = new Set(["battery", "storage_free", "storage", "free_storage", "vpn", "vpn_active", "apps", "app_versions"]);
  for (const [k, v] of Object.entries(info)) {
    const s = scalar(v);
    if (!known.has(k) && s !== null) facts.push({ label: k, value: String(s) });
  }
  return facts;
}

function appVersions(info: Info, pinned: Record<string, string>): { pkg: string; version: string; bad: boolean }[] {
  const apps = (info.apps ?? info.app_versions ?? {}) as Record<string, unknown>;
  const pkgs = new Set([...Object.keys(apps), ...Object.keys(pinned)]);
  return [...pkgs].map((pkg) => {
    const version = scalar(apps[pkg]) === null ? "—" : String(apps[pkg]);
    return { pkg, version, bad: !!pinned[pkg] && pinned[pkg] !== version };
  });
}

function LiveScreen({ device, onClose }: { device: string; onClose: () => void }) {
  const [frame, setFrame] = useState<string | null>(null);
  const [state, setState] = useState("Подключаюсь…");
  useEffect(() => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/api/devices/${encodeURIComponent(device)}/live`);
    ws.onopen = () => setState("Ждём первый кадр с телефона…");
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "frame") {
          setFrame(msg.jpeg_b64);
          setState(`кадр ${new Date((msg.ts ?? Date.now() / 1000) * 1000).toLocaleTimeString("ru-RU")}`);
        } else if (msg.type === "live.ended") {
          setState("Трансляция прервалась: телефон не отдаёт экран. Закройте окно и откройте снова.");
        }
      } catch {
        /* not json */
      }
    };
    ws.onclose = (ev) => {
      const why: Record<number, string> = {
        4401: "Сессия истекла — войдите заново",
        4403: "Отклонено: чужой источник запроса",
        4404: "Устройство не найдено",
      };
      setState(why[ev.code] ?? "Соединение закрыто");
    };
    return () => ws.close();
  }, [device]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="farm-modal-backdrop" onClick={onClose}>
      <div className="farm-modal farm-live" onClick={(e) => e.stopPropagation()}>
        <header className="drawer-head">
          <div>
            <h2>Живой экран · {device}</h2>
            <span className="muted">{state}</span>
          </div>
          <button className="btn btn-ghost" onClick={onClose} aria-label="Закрыть">
            ✕
          </button>
        </header>
        <div className="farm-live-screen">
          {frame ? <img src={`data:image/jpeg;base64,${frame}`} alt={`Экран ${device}`} /> : <span className="muted">нет кадра</span>}
        </div>
        <p className="muted farm-live-note">Только просмотр, ~1 кадр в секунду. Трансляция останавливается, когда окно закрыто.</p>
      </div>
    </div>
  );
}

function Lightbox({ src, onClose }: { src: string; onClose: () => void }) {
  return (
    <div className="lightbox" onClick={onClose}>
      <img src={src} alt="" />
    </div>
  );
}

function pinnedToText(p: Record<string, string>): string {
  return Object.entries(p)
    .map(([k, v]) => `${k} = ${v}`)
    .join("\n");
}

function parsePinned(text: string): { value: Record<string, string>; error: string | null } {
  const value: Record<string, string> = {};
  for (const [i, raw] of text.split("\n").entries()) {
    const line = raw.trim();
    if (!line) continue;
    const m = line.match(/^([A-Za-z][\w]*(?:\.[\w]+)+)\s*[=:]\s*(\S*)$/);
    if (!m) return { value, error: `Строка ${i + 1}: нужен вид «com.instagram.android = 440.0.0.46.86»` };
    value[m[1]] = m[2];
  }
  return { value, error: null };
}

function DeviceDrawer({ device, onClose }: { device: Device; onClose: () => void }) {
  const refresh = useFarmRefresh();
  const [vpn, setVpn] = useState(device.require_vpn);
  const [pinned, setPinned] = useState(pinnedToText(device.pinned_apps));
  const [note, setNote] = useState<string | null>(null);
  const parsed = parsePinned(pinned);
  const save = useMutation({
    mutationFn: () =>
      api.patch<CommandResult>(`/devices/${encodeURIComponent(device.name)}`, { require_vpn: vpn, pinned_apps: parsed.value }),
    onSuccess: (res) => {
      setNote(commandNote(res, "Сохранено на ферме"));
      refresh();
    },
  });

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()}>
        <header className="drawer-head">
          <div>
            <h2>{device.name}</h2>
            <span className="muted">серийный номер {device.serial}</span>
          </div>
          <button className="btn btn-ghost" onClick={onClose} aria-label="Закрыть">
            ✕
          </button>
        </header>
        <div className="drawer-body">
          <label className="farm-check">
            <input type="checkbox" checked={vpn} onChange={(e) => setVpn(e.target.checked)} />
            <span>Требовать VPN — без него задания на этом телефоне не запускаются</span>
          </label>
          <label>
            Закреплённые версии приложений
            <textarea
              rows={6}
              value={pinned}
              onChange={(e) => setPinned(e.target.value)}
              placeholder="com.instagram.android = 440.0.0.46.86"
            />
            <span className="muted">Проверка здоровья падает, если приложение обновилось само. Пустое значение — версия не проверяется.</span>
          </label>
          {parsed.error && <div className="error-note">{parsed.error}</div>}
          <ErrorNote error={save.error} />
          {note && <div className="farm-note">{note}</div>}
          <div className="drawer-actions">
            <button className="btn btn-primary" disabled={!!parsed.error || save.isPending} onClick={() => save.mutate()}>
              {save.isPending ? "Сохраняю…" : "Сохранить"}
            </button>
          </div>
        </div>
      </aside>
    </div>
  );
}

function DeviceCard({
  device,
  onLive,
  onEdit,
  onShot,
}: {
  device: Device;
  onLive: () => void;
  onEdit: () => void;
  onShot: (src: string) => void;
}) {
  const refresh = useFarmRefresh();
  const [note, setNote] = useState<string | null>(null);
  const st = device.status;
  const cmd = useMutation({
    mutationFn: (action: "pause" | "resume" | "health" | "screenshot") =>
      api.post<CommandResult<{ jpeg_b64?: string }>>(`/devices/${encodeURIComponent(device.name)}/${action}`),
    onSuccess: (res, action) => {
      if (action === "screenshot" && res.status === "done" && res.result?.jpeg_b64) {
        onShot(`data:image/jpeg;base64,${res.result.jpeg_b64}`);
        setNote(null);
      } else {
        const done = { pause: "Поставлено на паузу", resume: "Снято с паузы", health: "Проверено", screenshot: "Снимок получен" };
        setNote(commandNote(res, done[action]));
      }
      refresh();
    },
  });
  const online = st?.adb_state === "device";
  const facts = st ? infoFacts(st.info ?? {}) : [];
  const apps = appVersions(st?.info ?? {}, device.pinned_apps);
  const busyAction = cmd.isPending ? cmd.variables : null;

  return (
    <section className={`card device-card${st && !st.ok ? " device-bad" : ""}`}>
      <header className="device-head">
        <div>
          <h2>
            <span className={`dot ${online ? (st?.ok ? "dot-ok" : "dot-warn") : "dot-bad"}`} />
            {device.name}
          </h2>
          <span className="muted">
            {st ? ADB_LABEL[st.adb_state] ?? st.adb_state : "нет данных"} · проверено {agoTs(st?.checked_at)}
          </span>
        </div>
        <div className="chips">
          {st?.paused && <span className="badge badge-paused">Пауза</span>}
          {st?.busy && <span className="badge badge-running">Занят</span>}
          {device.require_vpn && <span className="badge">VPN обязателен</span>}
        </div>
      </header>

      {facts.length > 0 && (
        <dl className="facts">
          {facts.map((f) => (
            <div key={f.label} className={f.bad ? "bad" : ""}>
              <dt>{f.label}</dt>
              <dd>{f.value}</dd>
            </div>
          ))}
        </dl>
      )}

      {st && st.problems.length > 0 && (
        <ul className="problems">
          {st.problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}

      {apps.length > 0 && (
        <div className="apps">
          {apps.map((a) => (
            <span key={a.pkg} className={`app${a.bad ? " bad" : ""}`} title={a.bad ? `закреплена ${device.pinned_apps[a.pkg]}` : ""}>
              {a.pkg.split(".").slice(-2, -1)[0] ?? a.pkg} <b>{a.version}</b>
            </span>
          ))}
        </div>
      )}

      <div className="device-accounts">
        {device.accounts.length === 0 && <span className="muted">аккаунтов нет</span>}
        {device.accounts.map((a) => (
          <span key={a.id} className="chip chip-static">
            {PLATFORM_SHORT[a.platform] ?? a.platform} @{a.username}
            {a.mode && <span className="muted"> · {MODE_LABEL[a.mode] ?? a.mode}</span>}
          </span>
        ))}
      </div>

      <div className="muted device-jobs">
        {device.current_job ? (
          <div>
            Сейчас: {device.current_job.kind} · {device.current_job.account_id} с {timeTs(device.current_job.started_at)}
          </div>
        ) : (
          <div>Сейчас свободен</div>
        )}
        {device.next_job && (
          <div>
            Дальше: {device.next_job.kind} · {device.next_job.account_id} {agoTs(device.next_job.run_after)}
            {device.pending_jobs > 1 && ` (+${device.pending_jobs - 1})`}
          </div>
        )}
      </div>

      <ErrorNote error={cmd.error} />
      {note && <div className="farm-note">{note}</div>}

      <div className="device-actions">
        {st?.paused ? (
          <button className="btn" disabled={cmd.isPending} onClick={() => cmd.mutate("resume")}>
            ▶ Продолжить
          </button>
        ) : (
          <button className="btn" disabled={cmd.isPending} onClick={() => cmd.mutate("pause")}>
            ⏸ Пауза
          </button>
        )}
        <button className="btn" disabled={cmd.isPending} onClick={() => cmd.mutate("health")}>
          {busyAction === "health" ? "Проверяю…" : "Проверить"}
        </button>
        <button className="btn" disabled={cmd.isPending} onClick={() => cmd.mutate("screenshot")}>
          {busyAction === "screenshot" ? "Снимаю…" : "Скриншот"}
        </button>
        <button className="btn" onClick={onLive}>
          Живой экран
        </button>
        <button className="btn btn-ghost" onClick={onEdit}>
          Настройки
        </button>
      </div>
    </section>
  );
}

export function DevicesPage() {
  const status = useFarmStatus();
  const { data, error } = useQuery({
    queryKey: ["devices"],
    queryFn: () => api.get<Device[]>("/devices"),
    refetchInterval: 15_000,
  });
  const [live, setLive] = useState<string | null>(null);
  const [edit, setEdit] = useState<string | null>(null);
  const [shot, setShot] = useState<string | null>(null);
  const editing = data?.find((d) => d.name === edit) ?? null;
  const s = status.data;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Устройства</h1>
          {s && (
            <span className="muted">
              {s.online ? "ферма на связи" : "ферма офлайн"}
              {s.host && ` · ${s.host}`}
              {s.agent_version && ` · агент ${s.agent_version}`}
              {s.snapshot_at && ` · данные ${agoTs(s.snapshot_at)}`}
            </span>
          )}
        </div>
      </header>
      <FarmBanner />
      <ErrorNote error={error} />
      {data && data.length === 0 && (
        <Card>
          <Empty>Ферма ещё не прислала список телефонов. Запустите агент на Mac — устройства появятся здесь.</Empty>
        </Card>
      )}
      <div className="device-grid">
        {data?.map((d) => (
          <DeviceCard key={d.name} device={d} onLive={() => setLive(d.name)} onEdit={() => setEdit(d.name)} onShot={setShot} />
        ))}
      </div>
      {live && <LiveScreen device={live} onClose={() => setLive(null)} />}
      {editing && <DeviceDrawer device={editing} onClose={() => setEdit(null)} />}
      {shot && <Lightbox src={shot} onClose={() => setShot(null)} />}
    </div>
  );
}
