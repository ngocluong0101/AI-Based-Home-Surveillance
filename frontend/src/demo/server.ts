// A backend that lives in the browser, for the public demo: the same routes and answers as backend/ (see src/api), over made-up
// cameras and events kept in memory. A reload starts over. Nothing here is ever sent anywhere.
import { ApiError } from "../api/client";
import type {
  AppSettings,
  AppSettingsUpdate,
  NotificationCheck,
  Camera,
  CameraCreate,
  CameraStatus,
  CameraUpdate,
  ConnectionCheckBody,
  ConnectionCheckResult,
  EngineSummary,
  EventPage,
  EventStatus,
  LiveStatus,
  PersonEvent,
  StreamLink,
} from "../types";
import { peopleAt, snapshotUrl } from "./scene";

const CAPACITY = 8;
const DAY_MS = 86_400_000;
/** A camera that was just started reports nothing for this long, like the real engine. */
const STARTUP_MS = 1500;

interface DemoCamera extends Camera {
  startedAt: number;
}
interface World {
  cameras: DemoCamera[];
  events: PersonEvent[]; // newest first
  nextId: number;
  rng: () => number;
  settings: AppSettings; // the figures about snapshots are made up when they are read (settingsView)
}

let latencyMs = 60;
/** Tests turn the pretend network delay off. */
export const setLatency = (ms: number): void => {
  latencyMs = ms;
};

let world: World | null = null;

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const hex = (rng: () => number, n: number): string => Array.from({ length: n }, () => Math.floor(rng() * 16).toString(16)).join("");
const uuid = (rng: () => number): string => `${hex(rng, 8)}-${hex(rng, 4)}-4${hex(rng, 3)}-a${hex(rng, 3)}-${hex(rng, 12)}`;
const RUN_ID = "5d1c9f3e-7a42-4b0e-9c61-0e2f8a4b7d10";

/** The address as the backend shows it: the login is hidden. */
export function mask(uri: string): string {
  return uri.replace(/^(rtsps?:\/\/)[^@/]*@/i, "$1***@");
}

const SEED_CAMERAS: { name: string; uri: string; running: boolean; perDay: number; conf: number; absence: number }[] = [
  { name: "Front door", uri: "rtsp://***@192.168.1.21:554/stream1", running: true, perDay: 22, conf: 0.25, absence: 10 },
  { name: "Garage", uri: "rtsp://***@192.168.1.22:554/stream1", running: true, perDay: 9, conf: 0.25, absence: 10 },
  { name: "Living room", uri: "rtsp://***@192.168.1.23:554/h264", running: true, perDay: 14, conf: 0.4, absence: 10 },
  { name: "Backyard", uri: "rtsp://***@192.168.1.24:554/stream2", running: true, perDay: 11, conf: 0.25, absence: 20 },
  { name: "Garden shed", uri: "rtsp://***@192.168.1.25:554/stream1", running: false, perDay: 4, conf: 0.25, absence: 10 },
];

/** Moves `t` to a moment (not after `now`) when somebody is in front of the camera, so a snapshot always shows a person. */
function momentWithPerson(cameraId: number, t: number, now: number): number {
  for (let k = 0; k < 80; k++) {
    for (const sign of [1, -1]) {
      const tt = t + sign * k * 3000;
      if (tt <= now && peopleAt(cameraId, tt).length > 0) return tt;
    }
  }
  return Math.min(t, now);
}

function makeEvent(w: World, camera: Camera, t: number, status: EventStatus): PersonEvent {
  const person = peopleAt(camera.id, t)[0];
  const box: [number, number, number, number] = person ? [person.x, person.y, person.w, person.h] : [0.4, 0.4, 0.15, 0.4];
  const floor = Math.max(camera.conf_threshold, 0.5);
  return {
    event_id: uuid(w.rng),
    camera_id: camera.id,
    pipeline_run_id: RUN_ID,
    type: "PERSON_DETECTED",
    track_id: person?.id ?? 1,
    confidence: Math.round((floor + w.rng() * (0.97 - floor)) * 10000) / 10000,
    timestamp: new Date(t).toISOString(),
    bbox: box.map((v) => Math.round(v * 10000) / 10000) as [number, number, number, number],
    snapshot_url: person ? snapshotUrl(camera.id, camera.name, [person], t) : null,
    status,
    notify_status: "sent",
  };
}

function build(now: number): World {
  const rng = mulberry32(20261106);
  const w: World = {
    cameras: [],
    events: [],
    nextId: SEED_CAMERAS.length + 1,
    rng,
    settings: {
      alerts_enabled: true,
      quiet_hours_enabled: false,
      quiet_start: "22:00",
      quiet_end: "06:00",
      snapshot_retention_days: 7,
      default_conf_threshold: 0.25,
      default_absence_timeout: 10,
      telegram_configured: true, // so that the test message can be tried; nothing is sent anywhere
      snapshot_count: 0,
      snapshot_bytes: 0,
      version: "demo",
    },
  };
  SEED_CAMERAS.forEach((s, i) => {
    w.cameras.push({
      id: i + 1,
      name: s.name,
      source_type: "rtsp",
      source_uri: s.uri,
      enabled: true,
      ai_enabled: true,
      desired_state: s.running ? "running" : "stopped",
      conf_threshold: s.conf,
      absence_timeout: s.absence,
      created_at: new Date(now - 30 * DAY_MS).toISOString(),
      updated_at: new Date(now - 30 * DAY_MS).toISOString(),
      startedAt: now - 3_600_000, // up for an hour
    });
  });

  const midnight = new Date(now);
  midnight.setHours(0, 0, 0, 0);
  const todayShare = Math.max((now - midnight.getTime()) / DAY_MS, 0.1);
  for (const [i, s] of SEED_CAMERAS.entries()) {
    const camera = w.cameras[i];
    for (let day = 0; day < 3; day++) {
      if (day === 0 && !s.running) continue; // the stopped camera has nothing today
      const dayStart = midnight.getTime() - day * DAY_MS;
      const n = day === 0 ? Math.max(3, Math.round(s.perDay * todayShare)) : Math.round(s.perDay * (0.8 + w.rng() * 0.4));
      for (let k = 0; k < n; k++) {
        // daytime weighs more than the small hours
        const hour = day === 0 ? w.rng() * ((now - dayStart) / 3_600_000) : 6 + w.rng() * 16;
        const t = momentWithPerson(camera.id, dayStart + hour * 3_600_000, now);
        const recent = now - t < 3 * 3_600_000;
        w.events.push(makeEvent(w, camera, t, w.rng() < (recent ? 0.85 : 0.12) ? "new" : "acknowledged"));
      }
    }
  }
  w.events.sort((a, b) => b.timestamp.localeCompare(a.timestamp));
  return w;
}

/** The world as it is now; built on first use. */
function get(): World {
  world ??= build(Date.now());
  return world;
}

/** Starts the world over (tests). */
export function resetDemo(now = Date.now()): void {
  world = build(now);
}

// ---- views

const wanted = (c: DemoCamera) => c.enabled && c.desired_state === "running";
const reporting = (c: DemoCamera, now: number) => wanted(c) && now - c.startedAt > STARTUP_MS;

function liveStatus(c: DemoCamera, now: number): LiveStatus {
  if (!reporting(c, now)) {
    return { camera_id: c.id, reporting: false, state: null, fps: null, last_frame_age: null, heartbeat_age: null, people: null };
  }
  return {
    camera_id: c.id,
    reporting: true,
    state: "RUNNING",
    fps: Math.round((29.8 + 0.3 * Math.sin(now / 3000 + c.id)) * 10) / 10,
    last_frame_age: 0.03,
    heartbeat_age: (now % 5000) / 1000,
    people: c.ai_enabled ? peopleAt(c.id, now).length : null,
  };
}

/** What the heartbeat says about every camera, as the WebSocket would push it. */
export function liveStatuses(now = Date.now()): LiveStatus[] {
  return get().cameras.map((c) => liveStatus(c, now));
}

function strip(c: DemoCamera): Camera {
  const { startedAt, ...camera } = c;
  void startedAt;
  return camera;
}

function statusOf(c: DemoCamera, now = Date.now()): CameraStatus {
  const { camera_id, ...stream } = liveStatus(c, now);
  void camera_id;
  return {
    camera_id: c.id,
    enabled: c.enabled,
    desired_state: c.desired_state,
    should_run: wanted(c),
    engine: { manager: "reachable", state: wanted(c) ? "running" : "stopped", in_engine: wanted(c), applied: true, starts: 1, last_exit_code: null },
    stream,
  };
}

function engineSummary(): EngineSummary {
  const running = get().cameras.filter(wanted).length;
  return { manager: "reachable", state: "running", capacity: CAPACITY, running, wanted: running, applied: true, room: Math.max(CAPACITY - running, 0) };
}

/** A person is detected by a running camera: the event is stored and returned (null when nobody is in view right now). */
export function newLiveEvent(now = Date.now()): PersonEvent | null {
  const w = get();
  const candidates = w.cameras.filter((c) => reporting(c, now) && c.ai_enabled && peopleAt(c.id, now).length > 0);
  if (!candidates.length) return null;
  const camera = candidates[Math.floor(w.rng() * candidates.length)];
  const event = makeEvent(w, camera, now, "new");
  event.notify_status = alertStatusAt(now);
  w.events.unshift(event);
  return event;
}

// ---- settings

const SNAPSHOT_BYTES = 86_000; // what a 1080p JPEG roughly weighs, for the made-up "in use" figure
const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;
const minutesOf = (hhmm: string): number => Number(hhmm.slice(0, 2)) * 60 + Number(hhmm.slice(3));

/** The settings as the page reads them: the stored ones plus the made-up storage figures. */
function settingsView(): AppSettings {
  const s = get().settings;
  const count = get().events.filter((e) => e.snapshot_url).length;
  return { ...s, snapshot_count: count, snapshot_bytes: count * SNAPSHOT_BYTES };
}

/** Is a moment inside the quiet hours? The start minute is included, the end minute is not; they may pass midnight. */
export function inQuietHours(s: AppSettings, at: Date): boolean {
  if (!s.quiet_hours_enabled || s.quiet_start === s.quiet_end) return false;
  const now = at.getHours() * 60 + at.getMinutes();
  const start = minutesOf(s.quiet_start);
  const end = minutesOf(s.quiet_end);
  return start < end ? now >= start && now < end : now >= start || now < end;
}

/** What would happen to the Telegram alert of an event at this moment: it is sent, or held back on purpose. */
export function alertStatusAt(now = Date.now()): "sent" | "skipped" {
  const s = get().settings;
  return !s.alerts_enabled || inQuietHours(s, new Date(now)) ? "skipped" : "sent";
}

function updateSettings(body: AppSettingsUpdate): AppSettings {
  const refuse = (message: string): never => {
    throw new ApiError(422, message);
  };
  const next = { ...get().settings };
  for (const [key, value] of Object.entries(body)) {
    if (value === null || value === undefined) refuse(`${key}: cannot be null`);
    switch (key) {
      case "alerts_enabled":
      case "quiet_hours_enabled":
        if (typeof value !== "boolean") refuse(`${key}: must be true or false`);
        break;
      case "quiet_start":
      case "quiet_end":
        if (typeof value !== "string" || !HHMM.test(value)) refuse(`${key}: must look like 22:00`);
        break;
      case "snapshot_retention_days":
        if (!Number.isInteger(value) || (value as number) < 1 || (value as number) > 365) refuse("snapshot_retention_days: must be a whole number from 1 to 365");
        break;
      case "default_conf_threshold":
        if (typeof value !== "number" || value < 0 || value > 1) refuse("default_conf_threshold: must be from 0 to 1");
        break;
      case "default_absence_timeout":
        if (typeof value !== "number" || value < 0 || value > 86400) refuse("default_absence_timeout: must be from 0 to 86400");
        break;
      default:
        refuse(`${key}: extra inputs are not permitted`);
    }
    Object.assign(next, { [key]: value });
  }
  if (next.quiet_hours_enabled && next.quiet_start === next.quiet_end) refuse("quiet_start and quiet_end must differ");
  get().settings = next;
  return settingsView();
}

function testNotification(): NotificationCheck {
  return { ok: true, message: "Demo: nothing was sent, this page runs on made-up data. On a real installation the message goes to your Telegram." };
}

function testConnection(body: ConnectionCheckBody): ConnectionCheckResult {
  let type: "rtsp" | "file";
  let uri: string;
  if ("camera_id" in body) {
    const c = get().cameras.find((x) => x.id === body.camera_id);
    if (!c) throw new ApiError(404, "Camera not found");
    type = c.source_type;
    uri = c.source_uri;
  } else {
    type = body.source_type;
    uri = body.source_uri;
  }
  const none = { codec: null, width: null, height: null, latency_ms: null };
  if (type === "file") {
    const ok = /\.(mp4|mkv|avi|mov)$/i.test(uri);
    return {
      ok,
      summary: ok ? "The file can be read" : "This does not look like a video file",
      checks: [{ name: "file", ok, message: ok ? "The file exists and is readable" : "Expected a path ending in .mp4, .mkv, .avi or .mov" }],
      ...none,
    };
  }
  if (!/^rtsps?:\/\/.+/i.test(uri)) {
    return { ok: false, summary: "Not an RTSP address", checks: [{ name: "connect", ok: false, message: "An RTSP address starts with rtsp://" }], ...none };
  }
  const connect = { name: "connect" as const, ok: true, message: "Reached the camera" };
  if (/fail|wrong|bad/i.test(uri)) {
    return { ok: false, summary: "The camera did not accept the username and password", checks: [connect, { name: "auth", ok: false, message: "Login refused" }], ...none };
  }
  return {
    ok: true,
    summary: "H.264 1920x1080",
    checks: [
      connect,
      { name: "auth", ok: true, message: "Login accepted" },
      { name: "stream", ok: true, message: "The camera announces a video stream" },
      { name: "codec", ok: true, message: "H.264" },
      { name: "resolution", ok: true, message: "1920x1080" },
    ],
    codec: "H.264",
    width: 1920,
    height: 1080,
    latency_ms: 142,
  };
}

function eventPage(q: URLSearchParams): EventPage {
  const limit = Number(q.get("limit") ?? 50);
  const offset = Number(q.get("offset") ?? 0);
  const camera = q.get("camera_id");
  const status = q.get("status");
  const from = q.get("from") === null ? null : new Date(q.get("from")!).toISOString();
  const to = q.get("to") === null ? null : new Date(q.get("to")!).toISOString();
  const hits = get().events.filter(
    (e) =>
      (camera === null || e.camera_id === Number(camera)) &&
      (status === null || e.status === status) &&
      (from === null || e.timestamp >= from) &&
      (to === null || e.timestamp < to),
  );
  return { items: hits.slice(offset, offset + limit), total: hits.length, limit, offset };
}

// ---- the routes

function findCamera(id: string): DemoCamera {
  const c = get().cameras.find((x) => x.id === Number(id));
  if (!c) throw new ApiError(404, "Camera not found");
  return c;
}

function createCamera(body: CameraCreate): Camera {
  const w = get();
  const name = body.name?.trim();
  if (!name) throw new ApiError(422, "name: Field required");
  if (w.cameras.some((c) => c.name.toLowerCase() === name.toLowerCase())) throw new ApiError(409, "A camera with this name already exists");
  const now = new Date().toISOString();
  const c: DemoCamera = {
    id: w.nextId++,
    name,
    source_type: body.source_type,
    source_uri: mask(body.source_uri),
    enabled: body.enabled,
    ai_enabled: body.ai_enabled,
    desired_state: "stopped",
    conf_threshold: body.conf_threshold,
    absence_timeout: body.absence_timeout,
    created_at: now,
    updated_at: now,
    startedAt: 0,
  };
  w.cameras.push(c);
  return strip(c);
}

function updateCamera(c: DemoCamera, body: CameraUpdate): Camera {
  const w = get();
  if (body.name !== undefined) {
    const name = body.name.trim();
    if (!name) throw new ApiError(422, "name: Field required");
    if (w.cameras.some((x) => x.id !== c.id && x.name.toLowerCase() === name.toLowerCase())) throw new ApiError(409, "A camera with this name already exists");
    c.name = name;
  }
  if (body.source_type !== undefined) c.source_type = body.source_type;
  if (body.source_uri !== undefined) c.source_uri = mask(body.source_uri);
  if (body.enabled !== undefined) c.enabled = body.enabled;
  if (body.ai_enabled !== undefined) c.ai_enabled = body.ai_enabled;
  if (body.conf_threshold !== undefined) c.conf_threshold = body.conf_threshold;
  if (body.absence_timeout !== undefined) c.absence_timeout = body.absence_timeout;
  c.updated_at = new Date().toISOString();
  return strip(c);
}

function setWanted(c: DemoCamera, run: boolean): CameraStatus {
  if (run && !c.enabled) throw new ApiError(409, "This camera is disabled");
  if (run && c.desired_state !== "running") c.startedAt = Date.now();
  c.desired_state = run ? "running" : "stopped";
  return statusOf(c);
}

/** Answers one request of src/api the way the backend would. */
export async function handle<T>(path: string, init?: RequestInit): Promise<T> {
  if (latencyMs > 0) await new Promise((r) => setTimeout(r, latencyMs));
  const url = new URL(path, "http://demo.local");
  const method = (init?.method ?? "GET").toUpperCase();
  const body = typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : undefined;
  const p = url.pathname;
  const w = get();

  const answer = (): unknown => {
    if (p === "/api/cameras" && method === "GET") return w.cameras.map(strip);
    if (p === "/api/cameras" && method === "POST") return createCamera(body as CameraCreate);
    if (p === "/api/cameras/test-connection" && method === "POST") return testConnection(body as ConnectionCheckBody);
    if (p === "/api/engine") return engineSummary();
    if (p === "/api/settings" && method === "GET") return settingsView();
    if (p === "/api/settings" && method === "PATCH") return updateSettings(body as AppSettingsUpdate);
    if (p === "/api/settings/test-notification" && method === "POST") return testNotification();

    const one = p.match(/^\/api\/cameras\/(\d+)$/);
    if (one) {
      const c = findCamera(one[1]);
      if (method === "GET") return strip(c);
      if (method === "PATCH") return updateCamera(c, body as CameraUpdate);
      if (method === "DELETE") {
        w.cameras = w.cameras.filter((x) => x !== c); // like the backend's soft delete, its events stay
        return undefined;
      }
    }

    const action = p.match(/^\/api\/cameras\/(\d+)\/(status|stream|start|stop)$/);
    if (action) {
      const c = findCamera(action[1]);
      if (action[2] === "status") return statusOf(c);
      if (action[2] === "start") return setWanted(c, true);
      if (action[2] === "stop") return setWanted(c, false);
      const link: StreamLink = { camera_id: c.id, whep_url: "demo:", player_url: "", available: reporting(c, Date.now()) };
      return link;
    }

    if (p === "/api/events" && method === "GET") return eventPage(url.searchParams);
    const event = p.match(/^\/api\/events\/([0-9a-f-]+)$/);
    if (event) {
      const e = w.events.find((x) => x.event_id === event[1]);
      if (!e) throw new ApiError(404, "Event not found");
      if (method === "GET") return e;
      if (method === "PATCH") {
        const status = (body as { status?: EventStatus } | undefined)?.status;
        if (status !== "new" && status !== "acknowledged") throw new ApiError(422, "status: must be new or acknowledged");
        e.status = status;
        return e;
      }
    }
    throw new ApiError(404, "Not found");
  };
  return answer() as T;
}
