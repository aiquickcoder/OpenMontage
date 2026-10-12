import { NavLink, Outlet } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, type Overview } from "../api";

type NavItem = { to: string; label: string; children?: NavItem[] };

const NAV: NavItem[] = [
  { to: "/", label: "Обзор" },
  { to: "/briefs", label: "Производство" },
  { to: "/videos", label: "Видео" },
  { to: "/worlds", label: "Миры" },
  { to: "/characters", label: "Персонажи" },
  { to: "/planner", label: "План" },
  {
    to: "/campaigns",
    label: "Реклама",
    children: [
      { to: "/campaigns", label: "Кампании" },
      { to: "/inserts", label: "Вставки" },
      { to: "/advertisers", label: "Рекламодатели" },
    ],
  },
  { to: "/publications", label: "Публикации" },
  { to: "/devices", label: "Устройства" },
  { to: "/accounts", label: "Аккаунты" },
];

export function Layout({ onLogout }: { onLogout: () => void }) {
  const { data } = useQuery({ queryKey: ["overview"], queryFn: () => api.get<Overview>("/overview") });
  const awaiting = data?.awaiting_count ?? 0;
  const link = (item: NavItem, sub = false) => (
    <NavLink key={item.to} to={item.to} end={item.to === "/"} className={`nav-link${sub ? " nav-sub" : ""}`}>
      {item.label}
      {item.to === "/videos" && awaiting > 0 && <span className="nav-count">{awaiting}</span>}
    </NavLink>
  );
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">Studio</div>
        <nav>
          {NAV.map((item) =>
            item.children ? (
              <div key={item.label} className="nav-group" role="group" aria-label={item.label}>
                <span className="nav-group-title">{item.label}</span>
                {item.children.map((child) => link(child, true))}
              </div>
            ) : (
              link(item)
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
