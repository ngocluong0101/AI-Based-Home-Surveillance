// Types mirror backend/schemas.py. The browser only talks to /api (the dev server proxies it).

export type CameraState = "RUNNING" | "RECONNECTING";

export interface Camera {
  id: number;
  name: string;
  source_type: "file" | "rtsp";
  source_uri: string; // masked by the backend
  enabled: boolean;
  ai_enabled: boolean;
  desired_state: "running" | "stopped";
  conf_threshold: number;
  absence_timeout: number;
  created_at: string;
  updated_at: string;
}

export interface StreamInfo {
  reporting: boolean;
  state: CameraState | null;
  fps: number | null;
  last_frame_age: number | null;
  heartbeat_age: number | null;
}

export interface EngineInfo {
  manager: "reachable" | "unreachable" | "not_configured";
  state: string | null;
  in_engine: boolean | null;
  applied: boolean | null;
  starts: number | null;
  last_exit_code: number | null;
}

export interface CameraStatus {
  camera_id: number;
  enabled: boolean;
  desired_state: "running" | "stopped";
  should_run: boolean;
  engine: EngineInfo;
  stream: StreamInfo;
}

/** The camera.status WebSocket message: the heartbeat view of one camera. */
export interface LiveStatus extends StreamInfo {
  camera_id: number;
}

export interface StreamLink {
  camera_id: number;
  whep_url: string;
  player_url: string;
  available: boolean;
}

export interface PersonEvent {
  event_id: string;
  camera_id: number;
  pipeline_run_id: string;
  type: string;
  track_id: number;
  confidence: number;
  timestamp: string;
  bbox: [number, number, number, number]; // x, y, w, h relative to the frame
  snapshot_url: string | null;
  status: "new" | "acknowledged";
  notify_status: string;
}

export interface EventPage {
  items: PersonEvent[];
  total: number;
  limit: number;
  offset: number;
}

export interface EventFilters {
  cameraId?: number;
  status?: "new" | "acknowledged";
  from?: string; // ISO 8601
  to?: string; // ISO 8601, exclusive
  limit: number;
  offset: number;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // not JSON; keep the status text
    }
    throw new ApiError(response.status, detail);
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

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
  acknowledge: (id: string, status: "new" | "acknowledged") =>
    request<PersonEvent>(`/api/events/${id}`, { method: "PATCH", body: JSON.stringify({ status }) }),
};

