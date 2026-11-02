import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { PersonEvent } from "../types";
import { applyMessage, parseMessage } from "./messages";

export type Connection = "connecting" | "open" | "closed";

const RETRY_MS = [1000, 2000, 5000, 10000];

/** Keeps /api/ws open (reconnecting with a growing delay) and feeds the query cache. */
export function useRealtime(onNewEvent?: (event: PersonEvent) => void): Connection {
  const qc = useQueryClient();
  const [state, setState] = useState<Connection>("connecting");
  const onNewEventRef = useRef(onNewEvent);
  onNewEventRef.current = onNewEvent;

  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: number | undefined;
    let attempt = 0;
    let stopped = false;

    const connect = () => {
      setState("connecting");
      const scheme = location.protocol === "https:" ? "wss" : "ws";
      const socket = new WebSocket(`${scheme}://${location.host}/api/ws`);
      ws = socket;
      socket.onopen = () => {
        attempt = 0;
        setState("open");
        // Nothing is replayed on connect: anything missed while offline comes from the REST API.
        for (const key of ["events", "eventsToday", "eventsCount", "status", "cameras"]) {
          void qc.invalidateQueries({ queryKey: [key] });
        }
      };
      socket.onmessage = (e) => {
        const msg = typeof e.data === "string" ? parseMessage(e.data) : null;
        if (msg) applyMessage(qc, msg, (event) => onNewEventRef.current?.(event));
      };
      socket.onclose = () => {
        if (stopped || ws !== socket) return;
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
