import { useState, type FormEvent } from "react";
import { api } from "../api";
import { ErrorNote } from "../components/ui";

export function Login({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState("owner");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/auth/login", { username, password });
      onDone();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <form className="login-box" onSubmit={submit}>
        <h1>Studio</h1>
        <label>
          Логин
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
        </label>
        <label>
          Пароль
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            autoFocus
          />
        </label>
        <ErrorNote error={error} />
        <button className="btn btn-primary" disabled={busy || !password}>
          {busy ? "Входим…" : "Войти"}
        </button>
      </form>
    </div>
  );
}
