import { describe, expect, it } from "vitest";
import type { EngineSummary } from "./types";
import { describeDetection, formatSeconds } from "./utils/detection";
import { describeEngine } from "./utils/engine";

const engine = (o: Partial<EngineSummary> = {}): EngineSummary => ({
  manager: "reachable",
  state: "running",
  capacity: 8,
  running: 3,
  wanted: 3,
  applied: true,
  room: 5,
  ...o,
});

describe("describeEngine", () => {
  it("says nothing while it is loading or when no engine is managed", () => {
    expect(describeEngine(undefined)).toBeNull();
    expect(describeEngine(engine({ manager: "not_configured" }))).toBeNull();
  });

  it("tells that free places take a camera without interrupting the others", () => {
    const n = describeEngine(engine())!;
    expect(n.tone).toBe("ok");
    expect(n.places).toBe("3 of 8");
    expect(n.detail).toBe("5 places free: a new camera starts without interrupting the others.");
  });

  it("uses the singular for one free place", () => {
    expect(describeEngine(engine({ wanted: 7, room: 1 }))!.detail).toMatch(/^1 place free/);
  });

  it("warns that a full engine restarts for the next camera", () => {
    const n = describeEngine(engine({ wanted: 8, room: 0 }))!;
    expect(n.tone).toBe("warn");
    expect(n.detail).toMatch(/restarts the engine/);
  });

  it("does not claim more places in use than there are", () => {
    // 10 cameras wanted, 8 places: the engine is about to restart with more room
    expect(describeEngine(engine({ wanted: 10, room: 0 }))!.places).toBe("8 of 8");
  });

  it("says when the latest change is still being applied", () => {
    expect(describeEngine(engine({ applied: false }))!.detail).toMatch(/Applying/);
  });

  it("does not mention places when the manager keeps none spare", () => {
    const n = describeEngine(engine({ capacity: null, room: null }))!;
    expect(n.places).toBeNull();
    expect(n.detail).toBeNull();
  });

  it("is red when the manager cannot be reached", () => {
    const n = describeEngine(engine({ manager: "unreachable", state: null, capacity: null, room: null }))!;
    expect(n.tone).toBe("bad");
    expect(n.places).toBeNull();
  });

  it("is idle, not green, when the engine is stopped", () => {
    expect(describeEngine(engine({ state: "stopped", wanted: 0, room: 8 }))!.tone).toBe("idle");
  });
});

describe("formatSeconds", () => {
  it.each([
    [0, "0 s"],
    [10, "10 s"],
    [59.6, "1 min"],
    [60, "1 min"],
    [90, "1 min 30 s"],
    [3600, "1 h"],
    [5400, "1 h 30 min"],
    [86400, "24 h"],
    [-5, "0 s"],
  ])("%s -> %s", (input, out) => {
    expect(formatSeconds(input)).toBe(out);
  });
});

describe("describeDetection", () => {
  const cam = { ai_enabled: true, conf_threshold: 0.25, absence_timeout: 10 };

  it("shows the detector floor for a camera on the defaults", () => {
    expect(describeDetection(cam)).toEqual({ aiOff: false, minConfidence: "25%", stricter: false, repeatAfter: "10 s" });
  });

  it("shows a stricter threshold as such", () => {
    const d = describeDetection({ ...cam, conf_threshold: 0.6, absence_timeout: 90 });
    expect(d.minConfidence).toBe("60%");
    expect(d.stricter).toBe(true);
    expect(d.repeatAfter).toBe("1 min 30 s");
  });

  it("never shows less than the detector can give", () => {
    expect(describeDetection({ ...cam, conf_threshold: 0.1 }).minConfidence).toBe("25%");
    expect(describeDetection({ ...cam, conf_threshold: 0 }).stricter).toBe(false);
  });

  it("flags AI off", () => {
    expect(describeDetection({ ...cam, ai_enabled: false }).aiOff).toBe(true);
  });
});
