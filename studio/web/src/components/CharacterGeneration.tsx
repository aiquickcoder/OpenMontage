import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { ErrorNote } from "./ui";
import { charFileUrl, TASK_LABEL, type Character, type CharacterTask, type GenerateResult } from "../types/characters";
import "../characters.css";

/** Polls a character_generate task and, once done, lets the owner pick the turnaround variant. */
export function CharacterGeneration({
  characterId,
  taskId,
  onRestart,
}: {
  characterId: string;
  taskId: string;
  onRestart?: (r: GenerateResult) => void;
}) {
  const qc = useQueryClient();
  const task = useQuery({
    queryKey: ["task", taskId],
    queryFn: () => api.get<CharacterTask>(`/tasks/${encodeURIComponent(taskId)}`),
    refetchInterval: (q) => (q.state.data && ["done", "failed"].includes(q.state.data.status) ? false : 4000),
  });
  const status = task.data?.status;
  useEffect(() => {
    if (status === "done" || status === "failed") {
      qc.invalidateQueries({ queryKey: ["character", characterId] });
      qc.invalidateQueries({ queryKey: ["characters"] });
    }
  }, [status, characterId, qc]);
  const choose = useMutation({
    mutationFn: (variant: number) => api.post<Character>(`/characters/${encodeURIComponent(characterId)}/choose-variant`, { variant }),
    onSuccess: (c) => {
      qc.setQueryData(["character", characterId], c);
      qc.invalidateQueries({ queryKey: ["characters"] });
    },
  });

  const retry = useMutation({
    mutationFn: () =>
      api.post<GenerateResult>(`/characters/${encodeURIComponent(characterId)}/generate`, {
        variants: task.data?.args.variants ?? 2,
      }),
    onSuccess: (r) => {
      qc.setQueryData(["character", characterId], r.character);
      qc.invalidateQueries({ queryKey: ["characters"] });
      onRestart?.(r);
    },
  });

  if (task.error) return <ErrorNote error={task.error} />;
  if (!task.data) return <div className="muted">Загрузка задачи…</div>;
  const t = task.data;
  return (
    <div className="char-gen">
      <div className="char-gen-status">
        <span className={`badge char-task-${t.status}`}>{TASK_LABEL[t.status]}</span>
        {t.status === "queued" && <span className="muted">Ждёт свободного воркера — начнётся между брифами.</span>}
        {t.status === "running" && <span className="muted">Flow рисует {t.args.variants} вариант(а) листа…</span>}
        {t.status === "failed" && <span className="error-note">{t.error}</span>}
        {t.status === "done" && <span className="muted">Выберите лист, который станет основным.</span>}
      </div>
      {(t.status === "queued" || t.status === "running") && <div className="progress progress-active"><div className="progress-bar" style={{ width: t.status === "running" ? "60%" : "10%" }} /></div>}
      {t.result && t.result.variants.length > 0 && (
        <div className="char-variants">
          {t.result.variants.map((rel, i) => {
            const n = i + 1;
            return (
              <button
                key={rel}
                type="button"
                className={`char-variant${choose.isSuccess && choose.variables === n ? " char-variant-on" : ""}`}
                disabled={choose.isPending}
                onClick={() => choose.mutate(n)}
                title={`Выбрать вариант ${n}`}
              >
                <img src={charFileUrl(characterId, rel, 640)} alt={`Вариант ${n}`} loading="lazy" />
                <span>Вариант {n}</span>
              </button>
            );
          })}
        </div>
      )}
      {(t.status === "failed" || t.status === "done") && (
        <div className="char-actions">
          <button type="button" className="btn" disabled={retry.isPending} onClick={() => retry.mutate()}>
            {retry.isPending ? "Ставлю…" : t.status === "failed" ? "Повторить генерацию" : "Нарисовать заново"}
          </button>
          <span className="muted">Тратит кредиты Flow.</span>
        </div>
      )}
      <ErrorNote error={choose.error || retry.error} />
      {choose.isSuccess && <div className="muted">Вариант {choose.variables} стал основным листом.</div>}
    </div>
  );
}
