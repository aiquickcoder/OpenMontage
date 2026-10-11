import { NavLink, Outlet } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, type Overview } from "../api";

const NAV: { to: string; label: string; soon?: boolean }[] = [
  { to: "/", label: "Обзор" },
  { to: "/briefs", label: "Производство" },
  { to: "/videos", label: "Видео" },
  { to: "/characters", label: "Персонажи", soon: true },
  { to: "/publications", label: "Публикации", soon: true },
  { to: "/devices", label: "Устройства", soon: true },
  { to: "/accounts", label: "Аккаунты", soon: true },
];

export function Layout({ onLogout }: { onLogout: () => void }) {
  const { data } = useQuery({ queryKey: ["overview"], queryFn: () => api.get<Overview>("/overview") });
  const awaiting = data?.awaiting_count ?? 0;
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">Studio</div>
        <nav>
          {NAV.map((item) =>
            item.soon ? (
              <span key={item.to} className="nav-link nav-soon" title="Будет на следующих этапах">
                {item.label}
                <small>скоро</small>
              </span>
            ) : (
              <NavLink key={item.to} to={item.to} end={item.to === "/"} className="nav-link">
                {item.label}
                {item.to === "/videos" && awaiting > 0 && <span className="nav-count">{awaiting}</span>}
              </NavLink>
            ),
          )}
        </nav>
        <button className="btn btn-ghost logout" onClick={onLogout}>
          Выйти
        </button>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
