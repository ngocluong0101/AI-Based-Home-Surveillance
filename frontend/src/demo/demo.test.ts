import { beforeEach, describe, expect, it } from "vitest";
import type { AppSettings, Camera, CameraStatus, ConnectionCheckResult, EventPage, NotificationCheck, PersonEvent } from "../types";
import { FRAME_H, FRAME_W, peopleAt } from "./scene";
import { alertStatusAt, handle, inQuietHours, mask, resetDemo, setLatency } from "./server";

beforeEach(() => {
  setLatency(0);
  resetDemo();
});

const get = <T>(path: string) => handle<T>(path);
const send = <T>(path: string, method: string, body?: unknown) => handle<T>(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });

const NEW_CAMERA = { name: "Porch", source_type: "rtsp", source_uri: "rtsp://admin:secret@10.0.0.9:554/s", enabled: true, ai_enabled: true, conf_threshold: 0.25, absence_timeout: 10 };

describe("the demo backend: cameras", () => {
  it("has five cameras, one of them stopped, and never shows a login", async () => {
    const cameras = await get<Camera[]>("/api/cameras");
    expect(cameras.map((c) => c.name)).toEqual(["Front door", "Garage", "Living room", "Backyard", "Garden shed"]);
    expect(cameras.filter((c) => c.desired_state === "stopped").map((c) => c.name)).toEqual(["Garden shed"]);
    for (const c of cameras) expect(c.source_uri).toContain("***@");
  });

  it("masks a login and keeps the rest of the address", () => {
    expect(mask("rtsp://admin:secret@10.0.0.9:554/s?x=1")).toBe("rtsp://***@10.0.0.9:554/s?x=1");
    expect(mask("rtsp://10.0.0.9/s")).toBe("rtsp://10.0.0.9/s");
    expect(mask("file:///videos/a.mp4")).toBe("file:///videos/a.mp4");
  });

  it("adds a camera (stopped, address masked), refuses an empty or repeated name", async () => {
    const c = await send<Camera>("/api/cameras", "POST", NEW_CAMERA);
    expect(c.desired_state).toBe("stopped");
    expect(c.source_uri).not.toContain("secret");
    await expect(send("/api/cameras", "POST", { ...NEW_CAMERA, name: "  " })).rejects.toMatchObject({ status: 422 });
    await expect(send("/api/cameras", "POST", { ...NEW_CAMERA, name: "front DOOR" })).rejects.toMatchObject({ status: 409 });
  });

  it("changes a camera and deletes it; its events stay", async () => {
    const before = await get<EventPage>("/api/events?limit=1&offset=0");
    const updated = await send<Camera>("/api/cameras/1", "PATCH", { name: "Door", conf_threshold: 0.6 });
    expect([updated.name, updated.conf_threshold]).toEqual(["Door", 0.6]);
    await send("/api/cameras/1", "DELETE");
    await expect(get("/api/cameras/1")).rejects.toMatchObject({ status: 404 });
    expect((await get<EventPage>("/api/events?limit=1&offset=0")).total).toBe(before.total);
  });

  it("starts and stops: the status and the engine's places follow", async () => {
    const run = await send<CameraStatus>("/api/cameras/5/start", "POST");
    expect(run.should_run).toBe(true);
    expect((await get<{ wanted: number; room: number }>("/api/engine")).wanted).toBe(5);
    const stop = await send<CameraStatus>("/api/cameras/1/stop", "POST");
    expect([stop.should_run, stop.stream.reporting]).toEqual([false, false]);
    const engine = await get<{ wanted: number; capacity: number; room: number }>("/api/engine");
    expect([engine.wanted, engine.room]).toEqual([4, engine.capacity - 4]);
  });

  it("does not start a disabled camera", async () => {
    await send("/api/cameras/5", "PATCH", { enabled: false });
    await expect(send("/api/cameras/5/start", "POST")).rejects.toMatchObject({ status: 409 });
  });

  it("reports a running camera with fps and people, and a stopped one with nothing", async () => {
    const running = await get<CameraStatus>("/api/cameras/1/status");
    expect(running.stream).toMatchObject({ reporting: true, state: "RUNNING" });
    expect(running.stream.fps).toBeGreaterThan(29);
    const stopped = await get<CameraStatus>("/api/cameras/5/status");
    expect(stopped.stream).toMatchObject({ reporting: false, fps: null, people: null });
  });

  it("offers live video only where the camera is reporting", async () => {
    expect((await get<{ available: boolean }>("/api/cameras/1/stream")).available).toBe(true);
    expect((await get<{ available: boolean }>("/api/cameras/5/stream")).available).toBe(false);
  });
});

describe("the demo backend: events", () => {
  it("lists newest first, every event with a picture and a box inside the frame", async () => {
    const page = await get<EventPage>("/api/events?limit=200&offset=0");
    expect(page.total).toBeGreaterThan(60);
    const times = page.items.map((e) => e.timestamp);
    expect(times).toEqual([...times].sort().reverse());
    for (const e of page.items) {
      expect(e.snapshot_url).toMatch(/^data:image\/svg\+xml/);
      const [x, y, w, h] = e.bbox;
      expect(x).toBeGreaterThanOrEqual(0);
      expect(y).toBeGreaterThanOrEqual(0);
      expect(x + w).toBeLessThanOrEqual(1.0001);
      expect(y + h).toBeLessThanOrEqual(1.0001);
      expect(e.confidence).toBeGreaterThan(0.25);
      expect(e.confidence).toBeLessThan(1);
    }
  });

  it("filters by camera, status and day, and pages without losing or repeating any", async () => {
    const all = await get<EventPage>("/api/events?limit=200&offset=0");
    const byCamera = await Promise.all([1, 2, 3, 4, 5].map((id) => get<EventPage>(`/api/events?camera_id=${id}&limit=1&offset=0`)));
    expect(byCamera.reduce((n, p) => n + p.total, 0)).toBe(all.total);

    const midnight = new Date();
    midnight.setHours(0, 0, 0, 0);
    const today = await get<EventPage>(`/api/events?from=${encodeURIComponent(midnight.toISOString())}&limit=200&offset=0`);
    expect(today.total).toBeGreaterThan(0);
    expect(today.total).toBeLessThan(all.total);
    expect(today.items.every((e) => new Date(e.timestamp) >= midnight)).toBe(true);

    const unread = await get<EventPage>("/api/events?status=new&limit=200&offset=0");
    expect(unread.items.every((e) => e.status === "new")).toBe(true);

    const pages = [await get<EventPage>("/api/events?limit=7&offset=0"), await get<EventPage>("/api/events?limit=7&offset=7")];
    expect(new Set([...pages[0].items, ...pages[1].items].map((e) => e.event_id)).size).toBe(14);
    expect(pages[1].items[0].event_id).toBe(all.items[7].event_id);
  });

  it("acknowledges an event", async () => {
    const [first] = (await get<EventPage>("/api/events?status=new&limit=1&offset=0")).items;
    const done = await send<PersonEvent>(`/api/events/${first.event_id}`, "PATCH", { status: "acknowledged" });
    expect(done.status).toBe("acknowledged");
    expect((await get<PersonEvent>(`/api/events/${first.event_id}`)).status).toBe("acknowledged");
    await expect(send(`/api/events/${first.event_id}`, "PATCH", { status: "bogus" })).rejects.toMatchObject({ status: 422 });
    await expect(get("/api/events/00000000-0000-4000-a000-000000000000")).rejects.toMatchObject({ status: 404 });
  });
});

describe("the demo backend: test connection", () => {
  const check = (body: unknown) => send<ConnectionCheckResult>("/api/cameras/test-connection", "POST", body);

  it("passes an RTSP camera through all five steps", async () => {
    const r = await check({ source_type: "rtsp", source_uri: "rtsp://u:p@192.168.1.50/s" });
    expect(r.ok).toBe(true);
    expect(r.checks.map((c) => c.name)).toEqual(["connect", "auth", "stream", "codec", "resolution"]);
    expect([r.codec, r.width, r.height]).toEqual(["H.264", 1920, 1080]);
  });

  it("fails at the login for an address that says wrong, and never repeats the address", async () => {
    const r = await check({ source_type: "rtsp", source_uri: "rtsp://u:wrongpass@192.168.1.50/s" });
    expect(r.ok).toBe(false);
    expect(r.checks.at(-1)).toMatchObject({ name: "auth", ok: false });
    expect(JSON.stringify(r)).not.toContain("wrongpass");
  });

  it("checks a saved camera by id, a file by its name, and rejects a plain word as an address", async () => {
    expect((await check({ camera_id: 2 })).ok).toBe(true);
    expect((await check({ source_type: "file", source_uri: "/videos/a.mp4" })).ok).toBe(true);
    expect((await check({ source_type: "file", source_uri: "/videos/a.txt" })).ok).toBe(false);
    expect((await check({ source_type: "rtsp", source_uri: "hello" })).ok).toBe(false);
    await expect(check({ camera_id: 99 })).rejects.toMatchObject({ status: 404 });
  });
});

describe("the demo backend: settings", () => {
  const SETTINGS: AppSettings = {
    alerts_enabled: true,
    quiet_hours_enabled: true,
    quiet_start: "22:00",
    quiet_end: "06:00",
    snapshot_retention_days: 7,
    default_conf_threshold: 0.25,
    default_absence_timeout: 10,
    telegram_configured: true,
    snapshot_count: 0,
    snapshot_bytes: 0,
    version: "demo",
  };
  const hhmm = (d: Date) => `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  const hoursFromNow = (h: number) => new Date(Date.now() + h * 3_600_000);

  it("starts with the defaults, a Telegram that can be tried, and made-up storage figures", async () => {
    const s = await get<AppSettings>("/api/settings");
    expect(s).toMatchObject({
      alerts_enabled: true,
      quiet_hours_enabled: false,
      quiet_start: "22:00",
      quiet_end: "06:00",
      snapshot_retention_days: 7,
      default_conf_threshold: 0.25,
      default_absence_timeout: 10,
      telegram_configured: true,
    });
    expect(s.snapshot_count).toBeGreaterThan(50);
    expect(s.snapshot_bytes).toBeGreaterThan(s.snapshot_count);
  });

  it("changes only what is sent and keeps it", async () => {
    const changed = await send<AppSettings>("/api/settings", "PATCH", { alerts_enabled: false, snapshot_retention_days: 3 });
    expect([changed.alerts_enabled, changed.snapshot_retention_days, changed.quiet_start]).toEqual([false, 3, "22:00"]);
    expect((await get<AppSettings>("/api/settings")).alerts_enabled).toBe(false);
  });

  it("refuses what the real backend refuses, and changes nothing", async () => {
    const before = await get<AppSettings>("/api/settings");
    for (const bad of [
      { quiet_start: "25:00" },
      { quiet_end: "9:00" },
      { snapshot_retention_days: 0 },
      { snapshot_retention_days: 366 },
      { snapshot_retention_days: 7.5 },
      { default_conf_threshold: 1.5 },
      { default_absence_timeout: -1 },
      { alerts_enabled: null },
      { unknown_setting: 1 },
    ]) {
      await expect(send("/api/settings", "PATCH", bad), JSON.stringify(bad)).rejects.toMatchObject({ status: 422 });
    }
    expect(await get<AppSettings>("/api/settings")).toEqual(before);
  });

  it("does not take quiet hours that start and end together", async () => {
    await expect(send("/api/settings", "PATCH", { quiet_hours_enabled: true, quiet_end: "22:00" })).rejects.toMatchObject({ status: 422 });
    expect((await get<AppSettings>("/api/settings")).quiet_hours_enabled).toBe(false);
    await send("/api/settings", "PATCH", { quiet_start: "08:00", quiet_end: "08:00" }); // off: any time is fine
    await expect(send("/api/settings", "PATCH", { quiet_hours_enabled: true })).rejects.toMatchObject({ status: 422 });
  });

  it("says plainly that a test message reaches nobody", async () => {
    const r = await send<NotificationCheck>("/api/settings/test-notification", "POST");
    expect(r.ok).toBe(true);
    expect(r.message).toContain("Demo");
  });

  it("holds back the alert while alerts are off, and inside the quiet hours", async () => {
    expect(alertStatusAt()).toBe("sent");
    await send("/api/settings", "PATCH", { alerts_enabled: false });
    expect(alertStatusAt()).toBe("skipped");
    await send("/api/settings", "PATCH", { alerts_enabled: true, quiet_hours_enabled: true, quiet_start: hhmm(hoursFromNow(-1)), quiet_end: hhmm(hoursFromNow(1)) });
    expect(alertStatusAt()).toBe("skipped");
    await send("/api/settings", "PATCH", { quiet_start: hhmm(hoursFromNow(2)), quiet_end: hhmm(hoursFromNow(3)) });
    expect(alertStatusAt()).toBe("sent");
  });

  it("covers the start minute of the quiet hours but not the end, and passes midnight", () => {
    const at = (h: number, m: number) => new Date(2026, 0, 1, h, m, 30);
    expect(inQuietHours(SETTINGS, at(23, 30))).toBe(true);
    expect(inQuietHours(SETTINGS, at(3, 0))).toBe(true);
    expect(inQuietHours(SETTINGS, at(22, 0))).toBe(true);
    expect(inQuietHours(SETTINGS, at(6, 0))).toBe(false);
    expect(inQuietHours(SETTINGS, at(12, 0))).toBe(false);
    expect(inQuietHours({ ...SETTINGS, quiet_start: "13:00", quiet_end: "15:00" }, at(14, 59))).toBe(true);
    expect(inQuietHours({ ...SETTINGS, quiet_start: "13:00", quiet_end: "15:00" }, at(15, 0))).toBe(false);
    expect(inQuietHours({ ...SETTINGS, quiet_hours_enabled: false }, at(23, 0))).toBe(false);
  });
});

describe("the made-up scene", () => {
  it("is a pure function of the camera and the time", () => {
    expect(peopleAt(2, 1_800_000_123_456)).toEqual(peopleAt(2, 1_800_000_123_456));
  });

  it("keeps every person inside the frame, and people come and go on every camera", () => {
    for (const camera of [1, 2, 3, 4, 5]) {
      const counts = new Set<number>();
      for (let s = 0; s < 600; s++) {
        const people = peopleAt(camera, 1_800_000_000_000 + s * 1000);
        counts.add(people.length);
        for (const p of people) {
          expect(p.x).toBeGreaterThanOrEqual(0);
          expect(p.x + p.w).toBeLessThanOrEqual(1.0001);
          expect(p.y).toBeGreaterThanOrEqual(0);
          expect(p.y + p.h).toBeLessThanOrEqual(1.0001);
          expect(p.w * FRAME_W).toBeLessThan(p.h * FRAME_H); // taller than wide
        }
      }
      expect(counts.size).toBeGreaterThan(1);
    }
  });
});
