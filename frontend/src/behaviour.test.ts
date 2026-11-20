import { beforeEach, describe, expect, it } from "vitest";
import { eventsQuery } from "./api";
import type { Camera, EventPage, PersonEvent } from "./types";
import { emptyForm, formFromCamera, toCreatePayload, toUpdatePayload, validateForm } from "./utils/cameraForm";
import { describeEvent, EVENT_KINDS, isAlert, kindOf, TYPE_FILTERS } from "./utils/eventType";
import { defaultRectangle, MAX_ZONES, pointFromClick, polygonArea, sameZones, svgPoints, zoneErrors, zonesOf } from "./utils/zones";
import { handle, newLiveEvent, resetDemo, setLatency } from "./demo/server";

beforeEach(() => {
  setLatency(0);
  resetDemo();
});

const get = <T>(path: string) => handle<T>(path);
const send = <T>(path: string, method: string, body?: unknown) => handle<T>(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });

describe("event types", () => {
  const ev = (over: Partial<PersonEvent>): PersonEvent => ({
    event_id: "e", camera_id: 1, pipeline_run_id: "r", type: "PERSON_DETECTED", track_id: 12, track_ids: [12], zone_id: null,
    confidence: 0.9, timestamp: "2026-01-01T00:00:00Z", occurred_at: null, severity: "info", bbox: [0, 0, 1, 1], snapshot_url: null,
    status: "new", notify_status: "pending", verification: "not_required", verification_score: null, verification_reason: null, ...over,
  });

  it("names every type and falls back to a person event for one it does not know", () => {
    expect(Object.keys(EVENT_KINDS)).toEqual(["PERSON_DETECTED", "FALL", "FIGHT", "RESTRICTED_AREA"]);
    expect(kindOf("FALL").label).toBe("Fall detected");
    expect(kindOf("SOMETHING_NEW").label).toBe("Person detected"); // a newer engine must not break the page
  });

  it("says what happened to whom", () => {
    expect(describeEvent(ev({ type: "FALL" }))).toBe("Person 12 fell and has not got up");
    expect(describeEvent(ev({ type: "FIGHT", track_ids: [3, 7] }))).toBe("Persons 3 and 7 seem to be fighting");
    expect(describeEvent(ev({ type: "RESTRICTED_AREA", zone_id: 2 }))).toBe("Person 12 in restricted zone 2");
    expect(describeEvent(ev({ type: "FALL", track_ids: [] }))).toContain("Person 12"); // an older row without track_ids
  });

  it("calls a fall or a fight an alert, a restricted area a warning, a person nothing special", () => {
    expect(isAlert(ev({ severity: "high" }))).toBe(true);
    expect(isAlert(ev({ severity: "medium" }))).toBe(false);
    expect(TYPE_FILTERS.find((t) => t.id === "alerts")?.types).toEqual(["FALL", "FIGHT"]);
  });

  it("puts the chosen types in the list query, comma separated", () => {
    expect(eventsQuery({ limit: 10, offset: 0, types: ["FALL", "FIGHT"] })).toContain("type=FALL%2CFIGHT");
    expect(eventsQuery({ limit: 10, offset: 0, types: [] })).not.toContain("type=");
    expect(eventsQuery({ limit: 10, offset: 0 })).not.toContain("type=");
  });
});

describe("zones", () => {
  it("turns a click into a point of the picture, kept inside it", () => {
    const box = { left: 100, top: 50, width: 400, height: 200 };
    expect(pointFromClick(300, 150, box)).toEqual([0.5, 0.5]);
    expect(pointFromClick(0, 9999, box)).toEqual([0, 1]);
    expect(pointFromClick(1, 1, { ...box, width: 0 })[0]).toBe(0);
  });

  it("measures the area and refuses zones that are not polygons", () => {
    expect(polygonArea(defaultRectangle("restricted").points)).toBeCloseTo(0.16);
    expect(zoneErrors([defaultRectangle("restricted")])).toEqual([]);
    expect(zoneErrors([{ kind: "ignore", points: [[0, 0], [0.5, 0.5]] }])[0]).toContain("needs 3 to 32 points");
    expect(zoneErrors([{ kind: "ignore", points: [[0, 0], [0.5, 0.5], [1, 1]] }])[0]).toContain("no area");
    expect(zoneErrors(Array.from({ length: MAX_ZONES + 1 }, () => defaultRectangle("ignore")))[0]).toContain("At most 8");
  });

  it("compares zones by content and draws them for an SVG of 0..1", () => {
    const a = [defaultRectangle("lie_ok")];
    expect(sameZones(a, zonesOf([{ kind: "lie_ok", zone_id: undefined as never, points: a[0].points }]))).toBe(true);
    expect(sameZones(a, [defaultRectangle("restricted")])).toBe(false);
    expect(svgPoints([[0.1, 0.2], [0.3, 0.4]])).toBe("0.1,0.2 0.3,0.4");
  });
});

describe("the camera form and the rules", () => {
  const camera: Camera = {
    id: 4, name: "Hall", source_type: "rtsp", source_uri: "rtsp://***@h/s", enabled: true, ai_enabled: true, desired_state: "stopped",
    conf_threshold: 0.25, absence_timeout: 10, behaviors: ["fall"], fall_net: 0, fall_net_thr: 0.9, fall_net_k: 3, zones: [{ kind: "lie_ok", zone_id: 3, points: [[0.1, 0.5], [0.4, 0.5], [0.4, 0.9]] }],
    created_at: "", updated_at: "",
  };
  const filled = () => ({ ...emptyForm(), name: "Hall", sourceUri: "rtsp://h/s" });

  it("starts with no rules and sends what was chosen", () => {
    expect(emptyForm().behaviors).toEqual([]);
    const v = { ...filled(), behaviors: ["fall" as const, "zones" as const], zones: [defaultRectangle("restricted")] };
    expect(validateForm(v)).toEqual({});
    const body = toCreatePayload(v);
    expect(body.behaviors).toEqual(["fall", "zones"]);
    expect(body.zones).toEqual([defaultRectangle("restricted")]);
  });

  it("asks for a restricted zone when the restricted-area alert is on, and for real polygons", () => {
    expect(validateForm({ ...filled(), behaviors: ["zones"] }).zones).toContain("at least one restricted zone");
    expect(validateForm({ ...filled(), behaviors: ["zones"], zones: [defaultRectangle("lie_ok")] }).zones).toContain("restricted zone");
    expect(validateForm({ ...filled(), zones: [{ kind: "ignore", points: [[0, 0], [1, 1]] }] }).zones).toBeDefined();
    expect(validateForm({ ...filled(), behaviors: ["fall"] })).toEqual({}); // falls and fights need no zone
  });

  it("sends only the rules that changed when a camera is edited", () => {
    const form = formFromCamera(camera);
    expect(form.behaviors).toEqual(["fall"]);
    expect(toUpdatePayload(form, camera)).toEqual({});
    expect(toUpdatePayload({ ...form, behaviors: ["fall", "fight"] }, camera)).toEqual({ behaviors: ["fall", "fight"] });
    expect(toUpdatePayload({ ...form, behaviors: [] }, camera)).toEqual({ behaviors: [] });          // switching everything off is a change
    const moved = { ...form, zones: [{ ...form.zones[0], points: [[0.2, 0.5], [0.4, 0.5], [0.4, 0.9]] as [number, number][] }] };
    expect(toUpdatePayload(moved, camera)).toEqual({ zones: moved.zones });
    expect(toUpdatePayload({ ...form, behaviors: [...form.behaviors].reverse() }, camera)).toEqual({}); // the order is not a change
  });
});

describe("the demo backend: behaviour events and rules", () => {
  it("has falls, fights and restricted-area events in the history, with the new fields", async () => {
    const page = await get<EventPage>("/api/events?limit=200&offset=0");
    const types = new Set(page.items.map((e) => e.type));
    expect(types).toEqual(new Set(["PERSON_DETECTED", "FALL", "FIGHT", "RESTRICTED_AREA"]));
    const fall = page.items.find((e) => e.type === "FALL")!;
    expect(fall.severity).toBe("high");
    expect(Date.parse(fall.timestamp) - Date.parse(fall.occurred_at!)).toBe(2000); // reported two seconds after the impact
    const person = page.items.find((e) => e.type === "PERSON_DETECTED")!;
    expect(person.occurred_at).toBeNull();
    expect(person.severity).toBe("info");
  });

  it("filters by type like the real API", async () => {
    const alerts = await get<EventPage>("/api/events?limit=200&offset=0&type=FALL%2CFIGHT");
    expect(alerts.items.length).toBeGreaterThan(0);
    expect(alerts.items.every((e) => e.type === "FALL" || e.type === "FIGHT")).toBe(true);
  });

  it("keeps the rules of a camera, and numbers the zones that have no number", async () => {
    const cameras = await get<Camera[]>("/api/cameras");
    expect(cameras.find((c) => c.name === "Living room")?.behaviors).toEqual(["fall", "fight"]);
    const front = cameras.find((c) => c.name === "Front door")!;
    expect(front.behaviors).toEqual([]);
    const square: [number, number][] = [[0.1, 0.1], [0.9, 0.1], [0.5, 0.9]];
    const saved = await send<Camera>(`/api/cameras/${front.id}`, "PATCH", {
      behaviors: ["zones"], zones: [{ kind: "restricted", points: square }, { kind: "ignore", zone_id: 1, points: square }, { kind: "lie_ok", points: square }],
    });
    expect(saved.behaviors).toEqual(["zones"]);
    expect(saved.zones.map((z) => z.zone_id)).toEqual([2, 1, 3]); // 1 is taken by the second zone
    expect((await get<Camera>(`/api/cameras/${front.id}`)).zones).toHaveLength(3);
  });

  it("raises what the rules of a camera are for as the live events go on", () => {
    const seen = new Set<string>();
    for (let i = 0; i < 60; i++) {
      const e = newLiveEvent(Date.now() + i * 1000);
      if (e) seen.add(e.type);
    }
    expect(seen.has("PERSON_DETECTED")).toBe(true);
    expect([...seen].some((t) => t !== "PERSON_DETECTED")).toBe(true);
  });
});
