import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";

/** One SSE connection for the whole app: refetch whatever the server says changed. */
export function useServerEvents(enabled: boolean) {
  const qc = useQueryClient();
  useEffect(() => {
    if (!enabled) return;
    let es: EventSource | null = null;
    let retry: number | undefined;
    const open = () => {
      es = new EventSource("/api/events", { withCredentials: true });
      es.onmessage = (msg) => {
        const ev = JSON.parse(msg.data) as { type: string; id?: string };
        if (ev.type === "briefs") {
          qc.invalidateQueries({ queryKey: ["briefs"] });
          qc.invalidateQueries({ queryKey: ["overview"] });
          qc.invalidateQueries({ queryKey: ["videos"] });
        } else if (ev.type === "project" && ev.id) {
          qc.invalidateQueries({ queryKey: ["brief", ev.id] });
          qc.invalidateQueries({ queryKey: ["briefs"] });
        }
      };
      es.onerror = () => {
        es?.close();
        retry = window.setTimeout(open, 5000);
      };
    };
    open();
    return () => {
      es?.close();
      window.clearTimeout(retry);
    };
  }, [enabled, qc]);
}
