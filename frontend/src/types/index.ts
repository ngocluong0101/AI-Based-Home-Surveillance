// Types mirror backend/schemas.py.

export type CameraState = "RUNNING" | "RECONNECTING";
export type EventStatus = "new" | "acknowledged";

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
  status: EventStatus;
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
  status?: EventStatus;
  from?: string; // ISO 8601
  to?: string; // ISO 8601, exclusive
  limit: number;
  offset: number;
}
