import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { ErrorNote } from "./ui";
import { tomorrow, WORLD_TASK_LABEL, type StudioTask } from "../types/worlds";
import "../worlds.css";

/** Polls GET /api/tasks/{id} every 4s until done/failed; calls onFinish once when it settles. */
export function useStudioTask(taskId: string | null | undefined, onFinish?: (t: StudioTask) => void) {
  const task = useQuery({
    queryKey: ["task", taskId],
    queryFn: () => api.get<StudioTask>(`/tasks/${encodeURIComponent(taskId!)}`),
    enabled: !!taskId,
    refetchInterval: (q) => (q.state.data && ["done", "failed"].includes(q.state.data.status) ? false : 4000),
  });
  const cb = useRef(onFinish);
  cb.current = onFinish;
  const fired = useRef<string | null>(null);
  const status = task.data?.status;
  useEffect(() => {
    if (!task.data || fired.current === task.data.id) return;
    if (status === "done" || status === "failed") {
      fired.current = task.data.id;
      cb.current?.(task.data);
    }
  }, [status, task.data]);
  return task;
}

export function TaskLine({ task, running }: { task: StudioTask | undefined; running: string }) {
  if (!task) return <div className="muted">Загрузка задачи…</div>;
  return (
    <div className="world-task">
      <div className="char-gen-status">
        <span className={`badge char-task-${task.status}`}>{WORLD_TASK_LABEL[task.status]}</span>
        {(task.status === "queued" || task.status === "running") && <span className="muted">{running}</span>}
        {task.status === "failed" && <span className="error-note">{task.error}</span>}
      </div>
      {(task.status === "queued" || task.status === "running") && (
        <div className="progress progress-active">
          <div className="progress-bar" style={{ width: task.status === "running" ? "60%" : "10%" }} />
        </div>
      )}
    </div>
  );
}

/** "Спланировать неделю (ИИ)" — POST /worlds/{id}/plan-week, then poll the task. */
export function PlanWeekForm({ worldId, onDone, plannerLink = true }: { worldId: string; onDone?: () => void; plannerLink?: boolean }) {
  const qc = useQueryClient();
  const [start, setStart] = useState(tomorrow);
  const [days, setDays] = useState(7);
  const [wishes, setWishes] = useState("");
  const [taskId, setTaskId] = useState<string | null>(null);
  const plan = useMutation({
    mutationFn: () =>
      api.post<{ task_id: string }>(`/worlds/${encodeURIComponent(worldId)}/plan-week`, {
        start_date: start,
        days,
        wishes: wishes.trim(),
      }),
    onSuccess: (r) => setTaskId(r.task_id),
  });
  const task = useStudioTask(taskId, () => {
    qc.invalidateQueries({ queryKey: ["episodes"] });
    onDone?.();
  });
  const busy = plan.isPending || (!!task.data && ["queued", "running"].includes(task.data.status)) || (!!taskId && !task.data);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    plan.mutate();
  };
  const created = task.data?.result?.episodes;

  return (
    <form className="form" onSubmit={submit}>
      <label>
        Первый день
        <input type="date" value={start} onChange={(e) => setStart(e.target.value)} required />
      </label>
      <label>
        Дней
        <input type="number" min={1} max={14} value={days} onChange={(e) => setDays(Math.max(1, Math.min(14, Number(e.target.value) || 1)))} />
      </label>
      <label className="span-2">
        Пожелания
        <textarea
          rows={3}
          value={wishes}
          onChange={(e) => setWishes(e.target.value)}
          maxLength={2000}
          placeholder="Например: в выходные — серии про бабушку, в среду — праздник"
        />
      </label>
      <div className="span-2 form-foot">
        <ErrorNote error={plan.error || task.error} />
        <button className="btn btn-primary" disabled={busy || !start}>
          {busy ? "Планирую…" : "Спланировать (ИИ)"}
        </button>
      </div>
      {taskId && (
        <div className="span-2">
          <TaskLine task={task.data} running="Claude пишет эпизоды — обычно минуту-две." />
          {task.data?.status === "done" && (
            <div className="char-actions world-gap">
              <span className="muted">
                {Array.isArray(created) ? `Добавлено эпизодов: ${created.length}.` : "План готов."} Эпизоды — черновики, их нужно утвердить.
              </span>
              {plannerLink && (
                <Link className="btn" to={`/planner?world=${encodeURIComponent(worldId)}&week=${start}`}>
                  Открыть план
                </Link>
              )}
            </div>
          )}
        </div>
      )}
    </form>
  );
}
