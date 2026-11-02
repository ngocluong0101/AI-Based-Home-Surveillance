import type {
  Camera,
  CameraStatus,
  EventFilters,
  EventPage,
  EventStatus,
  PersonEvent,
  StreamLink,
} from "../types";
import { request } from "./client";

export { ApiError } from "./client";

export function eventsQuery(f: EventFilters): string {
  const q = new URLSearchParams({ limit: String(f.limit), offset: String(f.offset) });
  if (f.cameraId !== undefined) q.set("camera_id", String(f.cameraId));
  if (f.status) q.set("status", f.status);
  if (f.from) q.set("from", f.from);
  if (f.to) q.set("to", f.to);
  return q.toString();
}

export const api = {
  cameras: () => request<Camera[]>("/api/cameras"),
  cameraStatus: (id: number) => request<CameraStatus>(`/api/cameras/${id}/status`),
  cameraStream: (id: number) => request<StreamLink>(`/api/cameras/${id}/stream`),
  startCamera: (id: number) => request<CameraStatus>(`/api/cameras/${id}/start`, { method: "POST" }),
  stopCamera: (id: number) => request<CameraStatus>(`/api/cameras/${id}/stop`, { method: "POST" }),
  events: (f: EventFilters) => request<EventPage>(`/api/events?${eventsQuery(f)}`),
  event: (id: string) => request<PersonEvent>(`/api/events/${id}`),
  setEventStatus: (id: string, status: EventStatus) =>
    request<PersonEvent>(`/api/events/${id}`, { method: "PATCH", body: JSON.stringify({ status }) }),
};
