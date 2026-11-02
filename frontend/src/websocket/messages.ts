import { QueryClient } from "@tanstack/react-query";
import type { EventPage, LiveStatus, PersonEvent } from "../types";
import { markCreated } from "./recentEvents";

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

/** Folds one WebSocket message into the query cache. `onNewEvent` fires once per event id (not for duplicates). */
export function applyMessage(qc: QueryClient, msg: Message, onNewEvent?: (event: PersonEvent) => void): void {
  switch (msg.type) {
    case "event.created":
      if (!markCreated(msg.data.event_id)) return;
      qc.setQueryData(["event", msg.data.event_id], msg.data);
      // Paging and filters make patching the lists error-prone; the lists are cheap to refetch.
      void qc.invalidateQueries({ queryKey: ["events"] });
      void qc.invalidateQueries({ queryKey: ["eventsToday"] });
      void qc.invalidateQueries({ queryKey: ["eventsCount"] });
      onNewEvent?.(msg.data);
      break;
    case "event.updated":
      qc.setQueryData(["event", msg.data.event_id], msg.data);
      qc.setQueriesData<EventPage>({ queryKey: ["events"] }, (page) =>
        page && { ...page, items: page.items.map((e) => (e.event_id === msg.data.event_id ? msg.data : e)) },
      );
      void qc.invalidateQueries({ queryKey: ["eventsCount"] });
      break;
    case "camera.status":
      qc.setQueryData(liveKey(msg.data.camera_id), msg.data);
      break;
  }
}
