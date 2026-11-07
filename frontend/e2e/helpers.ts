import { expect, type APIRequestContext, type Page } from "@playwright/test";

/** What the Settings page holds on a new installation. */
const DEFAULT_SETTINGS = {
  alerts_enabled: true,
  quiet_hours_enabled: false,
  quiet_start: "22:00",
  quiet_end: "06:00",
  snapshot_retention_days: 7,
  default_conf_threshold: 0.25,
  default_absence_timeout: 10,
};

/** Every camera (and with it the engine's list) back to nothing, and the settings to their defaults, so that a test starts from the same place. */
export async function reset(request: APIRequest): Promise<void> {
  expect((await request.patch("/api/settings", { data: DEFAULT_SETTINGS })).ok()).toBeTruthy();
  const cameras = (await (await request.get("/api/cameras")).json()) as { id: number }[];
  for (const c of cameras) {
    await request.post(`/api/cameras/${c.id}/stop`);
    await request.delete(`/api/cameras/${c.id}`);
  }
  // the manager has the empty list and the engine process is gone
  await expect
    .poll(async () => (await (await request.get("/api/engine")).json()).wanted, { timeout: 15_000 })
    .toBe(0);
}

type APIRequest = APIRequestContext;

const BACKEND = "http://127.0.0.1:18765";

export interface NewCamera {
  name: string;
  source_type?: "file" | "rtsp";
  source_uri?: string;
  conf_threshold?: number;
  absence_timeout?: number;
  ai_enabled?: boolean;
}

/** A camera made through the API (faster than the form, for tests that are about something else). */
export async function makeCamera(request: APIRequest, c: NewCamera): Promise<{ id: number; name: string }> {
  const res = await request.post("/api/cameras", {
    data: { source_type: "file", source_uri: "file:///videos/e2e.mp4", ...c },
  });
  expect(res.status(), await res.text()).toBe(201);
  return res.json();
}

export async function startCamera(request: APIRequest, id: number): Promise<void> {
  expect((await request.post(`/api/cameras/${id}/start`)).ok()).toBeTruthy();
}

/** An event the engine would have posted, so that the Events page has something to show. */
export async function makeEvent(request: APIRequest, cameraId: number, trackId = 1): Promise<void> {
  const res = await request.post(`${BACKEND}/internal/events`, {   // not behind the dev server's proxy
    data: {
      schema_version: 1,
      event_id: crypto.randomUUID(),
      camera_id: cameraId,
      pipeline_run_id: "0f8fad5b-d9cb-469f-a165-70867728950e",
      type: "PERSON_DETECTED",
      track_id: trackId,
      confidence: 0.91,
      timestamp: new Date().toISOString(),
      bbox: [0.1, 0.2, 0.3, 0.4],
      snapshot: null,
    },
  });
  expect(res.status(), await res.text()).toBe(201);
}

export const engineNote = (page: Page) => page.locator(".engine-note");
