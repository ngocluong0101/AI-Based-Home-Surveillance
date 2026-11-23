/*
 * @author: Ngoc Luong <ngocluongg0101@gmail.com>
 */

// Types mirror backend/schemas.py.

export type CameraState = "RUNNING" | "RECONNECTING" | "ENDED";
export type EventStatus = "new" | "acknowledged";

/** What the engine saw (ROADMAP_V2 V2-3). */
export type EventType = "PERSON_DETECTED" | "FALL" | "FIGHT" | "RESTRICTED_AREA";
export type Severity = "info" | "medium" | "high";
/** The rules a camera can run. "zones" turns on the restricted-area alerts; lie_ok and ignore zones act whenever they exist. */
export type FallNet = 0 | 1 | 2;

export type BehaviorName = "fall" | "fight" | "zones";
export type ZoneKind = "restricted" | "lie_ok" | "ignore";

/** A polygon on the picture; points are [x, y] relative to the frame (0..1, y down). */
export interface Zone {
  kind: ZoneKind;
  zone_id: number;
  points: [number, number][];
}

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
  behaviors: BehaviorName[];
  zones: Zone[];
  /** What decides a fall (needs the "fall" rule): 0 the rules, 1 the skeleton network, 2 the network or the rules. */
  fall_net: FallNet;
  /** With the network: its score that counts as a fall (0-1) and how many such scores in a row make one. */
  fall_net_thr: number;
  fall_net_k: number;
  created_at: string;
  updated_at: string;
}

/** GET /api/engine: the engine as a whole. */
export interface EngineSummary {
  manager: "reachable" | "unreachable" | "not_configured";
  state: string | null;
  /** Places in the running engine (cameras it can take without a restart); null = unknown. */
  capacity: number | null;
  running: number | null;
  /** Cameras that should run now. */
  wanted: number;
  /** The engine manager has the current list. */
  applied: boolean | null;
  /** Free places; 0 = the next camera restarts the engine. null = unknown. */
  room: number | null;
}

export interface StreamInfo {
  reporting: boolean;
  state: CameraState | null;
  fps: number | null;
  last_frame_age: number | null;
  heartbeat_age: number | null;
  /** Tracked persons in the latest frame; null = the engine cannot tell (no recent frame, or an older engine). */
  people: number | null;
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
  type: EventType;
  track_id: number;
  /** The people involved (a fight has two). */
  track_ids: number[];
  zone_id: number | null;
  confidence: number;
  /** When the event was reported. */
  timestamp: string;
  /** When the behaviour began (a fall: the impact); null for older events and PERSON_DETECTED. */
  occurred_at: string | null;
  severity: Severity;
  bbox: [number, number, number, number]; // x, y, w, h relative to the frame
  snapshot_url: string | null;
  status: EventStatus;
  notify_status: string;
  /** The VLM's check of a fall or fight (V2-6). */
  verification: Verification;
  /** The VLM's probability of "yes", when it answered. */
  verification_score: number | null;
  verification_reason: string | null;
}

export type Verification = "not_required" | "pending" | "confirmed" | "rejected" | "unverified";

export interface EventPage {
  items: PersonEvent[];
  total: number;
  limit: number;
  offset: number;
}

export interface EventFilters {
  cameraId?: number;
  /** One or several types; none = all of them. */
  types?: EventType[];
  status?: EventStatus;
  /** One or several VLM states; none = all of them. */
  verification?: Verification[];
  from?: string; // ISO 8601
  to?: string; // ISO 8601, exclusive
  limit: number;
  offset: number;
}

export interface CameraCreate {
  name: string;
  source_type: "rtsp" | "file";
  source_uri: string;
  enabled: boolean;
  ai_enabled: boolean;
  conf_threshold: number;
  absence_timeout: number;
  behaviors?: BehaviorName[];
  fall_net?: FallNet;
  fall_net_thr?: number;
  fall_net_k?: number;
  /** zone_id may be left out: the next free number is used. */
  zones?: { kind: ZoneKind; zone_id?: number; points: [number, number][] }[];
}

export type CameraUpdate = Partial<CameraCreate>;

/** GET/PATCH /api/settings: what is changed from the Settings page, plus a few facts it cannot change. */
export interface AppSettings {
  alerts_enabled: boolean;
  quiet_hours_enabled: boolean;
  /** "HH:MM" in the server's local time. */
  quiet_start: string;
  quiet_end: string;
  snapshot_retention_days: number;
  /** What a new camera starts with. */
  default_conf_threshold: number;
  default_absence_timeout: number;
  telegram_configured: boolean;
  snapshot_count: number;
  snapshot_bytes: number;
  version: string;
}

export type AppSettingsUpdate = Partial<
  Pick<
    AppSettings,
    | "alerts_enabled"
    | "quiet_hours_enabled"
    | "quiet_start"
    | "quiet_end"
    | "snapshot_retention_days"
    | "default_conf_threshold"
    | "default_absence_timeout"
  >
>;

/** POST /api/settings/test-notification: a Telegram that does not work is ok=false with a reason. */
export interface NotificationCheck {
  ok: boolean;
  message: string;
}

/** Either an address to try, or a saved camera (its address, password included, never leaves the server). */
export type ConnectionCheckBody =
  | { camera_id: number; timeout?: number }
  | { source_type: "rtsp" | "file"; source_uri: string; timeout?: number };

export interface ConnectionCheckItem {
  name: "connect" | "auth" | "stream" | "codec" | "resolution" | "file";
  ok: boolean;
  message: string;
}

export interface ConnectionCheckResult {
  ok: boolean;
  summary: string;
  checks: ConnectionCheckItem[];
  codec: string | null;
  width: number | null;
  height: number | null;
  latency_ms: number | null;
}
