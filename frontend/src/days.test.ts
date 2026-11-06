import { describe, expect, it } from "vitest";
import { parseHash } from "./hooks/useHashRoute";
import { dayLabel, groupByDay } from "./utils/format";

// Local dates on purpose: "today" and "yesterday" mean the days of the person looking at the screen.
const at = (y: number, m: number, d: number, h = 12, min = 0): string => new Date(y, m - 1, d, h, min).toISOString();
const NOW = new Date(2026, 10, 5, 15, 0).getTime(); // 5 Nov 2026, 15:00 local

describe("dayLabel", () => {
  it("names today and yesterday, also just either side of midnight", () => {
    expect(dayLabel(at(2026, 11, 5, 0, 1), NOW)).toBe("Today");
    expect(dayLabel(at(2026, 11, 5, 23, 59), NOW)).toBe("Today");
    expect(dayLabel(at(2026, 11, 4, 23, 59), NOW)).toBe("Yesterday");
    expect(dayLabel(at(2026, 11, 4, 0, 1), NOW)).toBe("Yesterday");
  });

  it("gives the date for older days, with the year only when it is not this year", () => {
    expect(dayLabel(at(2026, 11, 3), NOW)).not.toMatch(/2026/);
    expect(dayLabel(at(2026, 11, 3), NOW)).toMatch(/3/);
    expect(dayLabel(at(2025, 11, 3), NOW)).toMatch(/2025/);
  });

  it("knows yesterday across a month boundary", () => {
    const first = new Date(2026, 11, 1, 9, 0).getTime(); // 1 Dec
    expect(dayLabel(at(2026, 11, 30), first)).toBe("Yesterday");
  });
});

describe("groupByDay", () => {
  const ev = (id: string, ts: string) => ({ id, timestamp: ts });

  it("keeps the order and starts a group where the day changes", () => {
    const items = [
      ev("a", at(2026, 11, 5, 14)),
      ev("b", at(2026, 11, 5, 9)),
      ev("c", at(2026, 11, 4, 22)),
      ev("d", at(2026, 11, 2, 8)),
    ];
    const groups = groupByDay(items, NOW);
    expect(groups.map((g) => g.items.map((i) => i.id))).toEqual([["a", "b"], ["c"], ["d"]]);
    expect(groups.slice(0, 2).map((g) => g.label)).toEqual(["Today", "Yesterday"]);
  });

  it("gives nothing for nothing", () => {
    expect(groupByDay([], NOW)).toEqual([]);
  });

  it("does not merge the same day when something else sits between (a list that is not sorted)", () => {
    const groups = groupByDay([ev("a", at(2026, 11, 5)), ev("b", at(2026, 11, 4)), ev("c", at(2026, 11, 5))], NOW);
    expect(groups.map((g) => g.items.length)).toEqual([1, 1, 1]);
  });
});

describe("the wall route", () => {
  it("is a page, and a camera in the address is the one in focus", () => {
    expect(parseHash("#/wall")).toEqual({ page: "wall", cameraId: null });
    expect(parseHash("#/wall/3")).toEqual({ page: "wall", cameraId: 3 });
  });
});
