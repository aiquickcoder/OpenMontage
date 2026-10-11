import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "./api";
import { Layout } from "./components/Layout";
import { Login } from "./pages/Login";
import { OverviewPage } from "./pages/Overview";
import { BriefsPage } from "./pages/Briefs";
import { BriefPage } from "./pages/Brief";
import { VideosPage } from "./pages/Videos";
import { useServerEvents } from "./useEvents";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      refetchOnWindowFocus: true,
      retry: (count, err) => !(err instanceof ApiError && err.status < 500) && count < 2,
    },
  },
});

function App() {
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.get<{ user: string }>("/auth/me"), retry: false });
  const [signedOut, setSignedOut] = useState(false);
  const authed = !!me.data && !signedOut;
  useServerEvents(authed);

  useEffect(() => {
    const onUnauthorized = () => setSignedOut(true);
    window.addEventListener("studio:unauthorized", onUnauthorized);
    return () => window.removeEventListener("studio:unauthorized", onUnauthorized);
  }, []);

  if (me.isLoading) return <div className="boot">Studio…</div>;
  if (!authed)
    return (
      <Login
        onDone={() => {
          setSignedOut(false);
          qc.invalidateQueries();
        }}
      />
    );

  const logout = async () => {
    await api.post("/auth/logout");
    qc.clear();
    setSignedOut(true);
  };

  return (
    <Routes>
      <Route element={<Layout onLogout={logout} />}>
        <Route index element={<OverviewPage />} />
        <Route path="briefs" element={<BriefsPage />} />
        <Route path="briefs/:id" element={<BriefPage />} />
        <Route path="videos" element={<VideosPage />} />
        <Route path="*" element={<OverviewPage />} />
      </Route>
    </Routes>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
