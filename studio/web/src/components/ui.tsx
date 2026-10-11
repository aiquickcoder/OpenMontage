import type { ReactNode } from "react";
import type { BriefState, StageRow } from "../api";
import { STAGE_LABEL, STATE_LABEL } from "../format";

export function StateBadge({ state }: { state: BriefState }) {
  return <span className={`badge badge-${state}`}>{STATE_LABEL[state] ?? state}</span>;
}

export function Progress({ done, total, active }: { done: number; total: number; active?: boolean }) {
  const pct = total ? Math.round((done / total) * 100) : 0;
  return (
    <div className={`progress${active ? " progress-active" : ""}`} title={`${done} из ${total} этапов`}>
      <div className="progress-bar" style={{ width: `${pct}%` }} />
    </div>
  );
}

export function StageRail({ stages }: { stages: StageRow[] }) {
  return (
    <ol className="rail">
      {stages.map((s) => (
        <li key={s.stage} className={`rail-step rail-${s.status}`}>
          <span className="rail-dot" />
          <span className="rail-label">{STAGE_LABEL[s.stage] ?? s.stage}</span>
        </li>
      ))}
    </ol>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="error-note">{error instanceof Error ? error.message : String(error)}</div>;
}

export function Card({ title, actions, children }: { title?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      {(title || actions) && (
        <header className="card-head">
          {title && <h2>{title}</h2>}
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}
