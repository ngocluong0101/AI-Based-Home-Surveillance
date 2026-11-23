/*
 * @author: Ngoc Luong <ngocluongg0101@gmail.com>
 */

import { describe, expect, it } from "vitest";
import { eventsQuery } from "./api";
import { VERIFICATION_FILTERS, verificationNote } from "./utils/eventType";

const base = { verification_reason: null as string | null, notify_status: "pending" };

describe("what the page says about the VLM's check", () => {
  it("says nothing when there is no check", () => {
    expect(verificationNote({ ...base, verification: "not_required" })).toBeNull();
  });
  it("says it is checking, and that the alert does not wait for ever", () => {
    const n = verificationNote({ ...base, verification: "pending" })!;
    expect(n.label).toBe("Checking…");
    expect(n.detail).toContain("goes out in a few seconds either way");
  });
  it("a rejected event says whether the alert had already gone out", () => {
    expect(verificationNote({ ...base, verification: "rejected", notify_status: "skipped", verification_reason: "on the sofa" })!.detail).toBe("No message was sent. on the sofa");
    expect(verificationNote({ ...base, verification: "rejected", notify_status: "sent", verification_reason: "on the sofa" })!.detail).toMatch(/^The alert was sent, with a note/);
  });
  it("an unchecked event is a warning, a confirmed one is ok", () => {
    expect(verificationNote({ ...base, verification: "unverified" })!.tone).toBe("warn");
    expect(verificationNote({ ...base, verification: "confirmed", verification_reason: "floor" })!.tone).toBe("ok");
  });
});

describe("the VLM filter", () => {
  it("is sent to the API as a comma separated list", () => {
    const f = { limit: 10, offset: 0 };
    expect(new URLSearchParams(eventsQuery({ ...f, verification: ["unverified", "pending"] })).get("verification")).toBe("unverified,pending");
    expect(new URLSearchParams(eventsQuery(f)).has("verification")).toBe(false);
  });
  it("offers every state a person may look for, once", () => {
    const states = VERIFICATION_FILTERS.flatMap((v) => v.states ?? []);
    expect(new Set(states)).toEqual(new Set(["confirmed", "rejected", "unverified", "pending"]));
    expect(states.length).toBe(4);
  });
});
