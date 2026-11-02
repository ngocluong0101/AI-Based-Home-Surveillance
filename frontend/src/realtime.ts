import { useEffect, useState } from "react";
import { QueryClient, useQueryClient } from "@tanstack/react-query";
import type { EventPage, LiveStatus, PersonEvent } from "./api";

export type Message =
  | { type: "event.created"; data: PersonEvent }
  | { type: "event.updated"; data: PersonEvent }
  | { type: "camera.status"; data: LiveStatus };

export const liveKey = (cameraId: number) => ["live", cameraId] as const;

export function parseMessage(text: string): Message | null {
  try {
    const m = JSON.parse(text);
    if (m && typeof m.type === "string" && m.data && typeof m.data === "object") {
      if (m.type === "event.created" || m.type === "event.updated" || m.type === "camera.status") return m as Message;
    }
  } catch {
    // ignore garbage
  }
  return null;
}

/** Folds one WebSocket message into the query cache. */
export function applyMessage(qc: QueryClient, msg: Message): void {
  switch (msg.type) {
    case "event.created":
      // Paging and filters make patching the lists error-prone; the lists are cheap to refetch.
      void qc.invalidateQueries({ queryKey: ["events"] });
      void qc.invalidateQueries({ queryKey: ["eventsToday"] });
      break;
    case "event.updated":
      qc.setQueriesData<EventPage>({ queryKey: ["events"] }, (page) =>
        page && { ...page, items: page.items.map((e) => (e.event_id === msg.data.event_id ? msg.data : e)) },
      );
      break;
    case "camera.status":
      qc.setQueryData(liveKey(msg.data.camera_id), msg.data);
      break;
  }
}

export type Connection = "connecting" | "open" | "closed";

const RETRY_MS = [1000, 2000, 5000, 10000];

/** Keeps /api/ws open (reconnecting with a growing delay) and feeds the query cache. */
export function useRealtime(): Connection {
  const qc = useQueryClient();
  const [state, setState] = useState<Connection>("connecting");

  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: number | undefined;
    let attempt = 0;
    let stopped = false;

    const connect = () => {
      setState("connecting");
      const scheme = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${scheme}://${location.host}/api/ws`);
      ws.onopen = () => {
        attempt = 0;
        setState("open");
        // Nothing is replayed on connect: anything missed while offline comes from the REST API.
        void qc.invalidateQueries({ queryKey: ["events"] });
        void qc.invalidateQueries({ queryKey: ["eventsToday"] });
        void qc.invalidateQueries({ queryKey: ["status"] });
      };
      ws.onmessage = (e) => {
        const msg = typeof e.data === "string" ? parseMessage(e.data) : null;
        if (msg) applyMessage(qc, msg);
      };
      ws.onclose = () => {
        if (stopped) return;
        setState("closed");
        // Pushed status is stale now; fall back to the polled /status.
        qc.removeQueries({ queryKey: ["live"] });
        timer = window.setTimeout(connect, RETRY_MS[Math.min(attempt++, RETRY_MS.length - 1)]);
      };
    };

    connect();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      ws?.close();
    };
  }, [qc]);

  return state;
}
