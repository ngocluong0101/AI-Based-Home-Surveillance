import { QueryClient } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { eventsQuery } from "./api";
import { parseHash } from "./hooks/useHashRoute";
import type { CameraStatus, EventPage, PersonEvent, StreamInfo } from "./types";
import { localDayStart, relativeTime } from "./utils/format";
import { healthOf, lastErrorOf, totalPeople } from "./utils/health";
import { applyMessage, liveKey, parseMessage } from "./websocket/messages";
import { resetRecentEvents } from "./websocket/recentEvents";

const stream = (over: Partial<StreamInfo> = {}): StreamInfo => ({
  reporting: true,
  state: "RUNNING",
  fps: 30,
  last_frame_age: 0.03,
  heartbeat_age: 1,
  people: 2,
  ...over,
});

const status = (over: Partial<CameraStatus["engine"]> = {}, should_run = true): CameraStatus => ({
  camera_id: 1,
  enabled: true,
  desired_state: should_run ? "running" : "stopped",
  should_run,
  engine: { manager: "reachable", state: "running", in_engine: true, applied: true, starts: 1, last_exit_code: null, ...over },
  stream: stream({ reporting: false, state: null, fps: null, last_frame_age: null, heartbeat_age: null, people: null }),
});

const event = (over: Partial<PersonEvent> = {}): PersonEvent => ({
  event_id: "e1",
  camera_id: 1,
  pipeline_run_id: "r1",
  type: "PERSON_DETECTED",
  track_id: 7,
  confidence: 0.9,
  timestamp: "2026-11-02T10:00:00Z",
  bbox: [0.1, 0.2, 0.3, 0.4],
  snapshot_url: null,
  status: "new",
  notify_status: "pending",
  ...over,
});

describe("healthOf", () => {
  it("is Online for a running, reporting stream", () => {
    expect(healthOf(status(), stream())).toEqual({ label: "Online", tone: "ok" });
  });
  it("shows Reconnecting from the heartbeat state", () => {
    expect(healthOf(status(), stream({ state: "RECONNECTING", fps: 0 })).label).toBe("Reconnecting");
  });
  it("warns when a running stream stopped producing frames", () => {
    expect(healthOf(status(), stream({ fps: 0, last_frame_age: 20 })).label).toBe("No frames");
  });
  it("is Stopped when the camera should not run and nothing reports", () => {
    expect(healthOf(status({}, false), stream({ reporting: false })).label).toBe("Stopped");
  });
  it("is Starting while the engine has not reported yet", () => {
    expect(healthOf(status(), stream({ reporting: false })).label).toBe("Starting");
  });
  it("flags a crashed engine and an unreachable manager", () => {
    expect(healthOf(status({ state: "backoff" }), stream({ reporting: false })).tone).toBe("bad");
    expect(healthOf(status({ manager: "unreachable" }), stream({ reporting: false })).label).toBe("Engine manager down");
  });
  it("is Unknown before the first status arrives", () => {
    expect(healthOf(undefined, undefined).label).toBe("Unknown");
  });
});

describe("totalPeople", () => {
  it("adds up the cameras that can tell", () => {
    expect(totalPeople([stream({ people: 2 }), stream({ people: 3 })])).toBe(5);
  });
  it("counts an empty room as 0, not as unknown", () => {
    expect(totalPeople([stream({ people: 0 })])).toBe(0);
  });
  it("skips cameras that are not reporting or cannot tell", () => {
    expect(totalPeople([stream({ people: 4 }), stream({ reporting: false, people: 9 }), stream({ people: null }), undefined])).toBe(4);
  });
  it("is unknown when nothing can tell", () => {
    expect(totalPeople([])).toBeNull();
    expect(totalPeople([stream({ people: null }), stream({ reporting: false }), undefined])).toBeNull();
  });
});

describe("lastErrorOf", () => {
  it("has nothing to say for a healthy camera", () => {
    expect(lastErrorOf(status(), stream())).toBeNull();
  });
  it("reports the exit code of a crashed engine", () => {
    expect(lastErrorOf(status({ state: "backoff", last_exit_code: 1 }), stream({ reporting: false }))).toContain("code 1");
  });
  it("reports a camera that stopped sending frames", () => {
    expect(lastErrorOf(status(), stream({ state: "RECONNECTING", last_frame_age: 12.4 }))).toContain("12 s");
  });
});

describe("parseMessage", () => {
  it("accepts the three message types", () => {
    expect(parseMessage(JSON.stringify({ type: "camera.status", data: { camera_id: 1 } }))?.type).toBe("camera.status");
  });
  it("drops garbage and unknown types", () => {
    expect(parseMessage("not json")).toBeNull();
    expect(parseMessage(JSON.stringify({ type: "other", data: {} }))).toBeNull();
    expect(parseMessage(JSON.stringify({ type: "event.created" }))).toBeNull();
  });
});

describe("applyMessage", () => {
  beforeEach(() => resetRecentEvents());

  it("stores the pushed camera status by camera", () => {
    const qc = new QueryClient();
    applyMessage(qc, { type: "camera.status", data: { camera_id: 3, ...stream() } });
    expect(qc.getQueryData<{ fps: number }>(liveKey(3))?.fps).toBe(30);
  });

  it("replaces an updated event in every cached page and in its own cache entry", () => {
    const qc = new QueryClient();
    const page: EventPage = { items: [event(), event({ event_id: "e2" })], total: 2, limit: 25, offset: 0 };
    qc.setQueryData(["events", { limit: 25, offset: 0 }], page);
    applyMessage(qc, { type: "event.updated", data: event({ status: "acknowledged" }) });
    const after = qc.getQueryData<EventPage>(["events", { limit: 25, offset: 0 }])!;
    expect(after.items.map((e) => e.status)).toEqual(["acknowledged", "new"]);
    expect(qc.getQueryData<PersonEvent>(["event", "e1"])?.status).toBe("acknowledged");
  });

  it("marks event lists and counters stale when an event is created, and tells the caller once", () => {
    const qc = new QueryClient();
    qc.setQueryData(["events", { limit: 25, offset: 0 }], { items: [], total: 0, limit: 25, offset: 0 });
    qc.setQueryData(["eventsToday", null], { items: [], total: 0, limit: 1, offset: 0 });
    const seen = vi.fn();
    applyMessage(qc, { type: "event.created", data: event() }, seen);
    expect(qc.getQueryState(["events", { limit: 25, offset: 0 }])?.isInvalidated).toBe(true);
    expect(qc.getQueryState(["eventsToday", null])?.isInvalidated).toBe(true);
    expect(seen).toHaveBeenCalledTimes(1);
  });

  it("ignores a duplicate event.created (same event_id)", () => {
    const qc = new QueryClient();
    const seen = vi.fn();
    applyMessage(qc, { type: "event.created", data: event() }, seen);
    applyMessage(qc, { type: "event.created", data: event() }, seen);
    applyMessage(qc, { type: "event.created", data: event({ event_id: "e2" }) }, seen);
    expect(seen.mock.calls.map(([e]) => e.event_id)).toEqual(["e1", "e2"]);
  });
});

describe("route", () => {
  it("reads the page and camera from the hash", () => {
    expect(parseHash("#/monitoring/2")).toEqual({ page: "monitoring", cameraId: 2 });
    expect(parseHash("#/events")).toEqual({ page: "events", cameraId: null });
  });
  it("falls back to the dashboard for nothing or nonsense", () => {
    expect(parseHash("")).toEqual({ page: "dashboard", cameraId: null });
    expect(parseHash("#/nope/abc")).toEqual({ page: "dashboard", cameraId: null });
  });
});

describe("formatting", () => {
  it("only includes the filters that are set in the query string", () => {
    expect(eventsQuery({ limit: 25, offset: 0 })).toBe("limit=25&offset=0");
    const q = new URLSearchParams(eventsQuery({ cameraId: 2, status: "new", from: "a", to: "b", limit: 5, offset: 10 }));
    expect([q.get("camera_id"), q.get("status"), q.get("from"), q.get("to"), q.get("offset")]).toEqual(["2", "new", "a", "b", "10"]);
  });
  it("turns a date input into local midnight, and the end day into the next midnight", () => {
    expect(localDayStart("")).toBeUndefined();
    const start = new Date(localDayStart("2026-03-01")!);
    const next = new Date(localDayStart("2026-03-01", 1)!);
    expect([start.getDate(), start.getHours()]).toEqual([1, 0]);
    expect([next.getDate(), next.getHours()]).toEqual([2, 0]);
  });
  it("describes how long ago something happened", () => {
    const now = Date.parse("2026-11-02T10:00:00Z");
    const ago = (s: number) => relativeTime(new Date(now - s * 1000).toISOString(), now);
    expect([ago(1), ago(30), ago(300), ago(7200)]).toEqual(["just now", "30 s ago", "5 min ago", "2 h ago"]);
  });
});
