import { agoTs, useFarmStatus } from "../farm";

/** Shown on every farm page while the Mac agent isn't heard from. */
export function FarmBanner() {
  const { data } = useFarmStatus();
  if (!data) return null;
  if (!data.online)
    return (
      <div className="farm-banner farm-banner-bad">
        <strong>Ферма офлайн — команды выполнятся при подключении</strong>
        <span className="muted">
          последний сигнал {agoTs(data.last_seen)}
          {data.queued_commands > 0 && ` · в очереди команд: ${data.queued_commands}`}
        </span>
      </div>
    );
  if (data.scheduler_alive === false)
    return (
      <div className="farm-banner farm-banner-warn">
        <strong>Ферма на связи, но планировщик остановлен</strong>
        <span className="muted">задания не запускаются · последний пульс планировщика {agoTs(data.scheduler_heartbeat_at)}</span>
      </div>
    );
  return null;
}
