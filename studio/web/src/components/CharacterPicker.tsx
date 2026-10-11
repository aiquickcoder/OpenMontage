import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { charFileUrl, type Character } from "../types/characters";
import "../characters.css";

/** Multi-select of non-archived library characters (brief form). Value: character ids. */
export function CharacterPicker({ value, onChange }: { value: string[]; onChange: (ids: string[]) => void }) {
  const { data, error } = useQuery({
    queryKey: ["characters", "", "", "0"],
    queryFn: () => api.get<Character[]>("/characters"),
  });
  const toggle = (id: string) => onChange(value.includes(id) ? value.filter((v) => v !== id) : [...value, id]);

  if (error) return <div className="muted">Библиотека персонажей недоступна</div>;
  if (!data) return <div className="muted">Загрузка…</div>;
  const ready = data.filter((c) => c.turnaround_path);
  if (ready.length === 0) return <div className="muted">В библиотеке нет готовых персонажей</div>;
  return (
    <div className="char-picker" role="group" aria-label="Персонажи из библиотеки">
      {ready.map((c) => {
        const on = value.includes(c.id);
        return (
          <button
            type="button"
            key={c.id}
            className={`char-pick${on ? " char-pick-on" : ""}`}
            aria-pressed={on}
            onClick={() => toggle(c.id)}
            title={c.description}
          >
            <img src={charFileUrl(c.id, c.turnaround_path, 320)} alt="" loading="lazy" />
            <span>{c.name}</span>
          </button>
        );
      })}
    </div>
  );
}
