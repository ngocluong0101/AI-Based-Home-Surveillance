import type { Zone, ZoneKind } from "../types";

/** A zone as the editor holds it: the number is given by the server when it is left out. */
export interface ZoneDraft {
  kind: ZoneKind;
  zone_id?: number;
  points: [number, number][];
}

export const MAX_ZONES = 8;
export const MIN_POINTS = 3;
export const MAX_POINTS = 32;

export const ZONE_LABEL: Record<ZoneKind, string> = {
  restricted: "Restricted area",
  lie_ok: "Bed or sofa (lying is fine)",
  ignore: "Ignore (TV, mirror)",
};

export const ZONE_COLOR: Record<ZoneKind, string> = { restricted: "#f87171", lie_ok: "#60a5fa", ignore: "#9ca3af" };

const clamp01 = (v: number): number => Math.min(1, Math.max(0, v));

/** A point from a click: relative to the box it happened in, kept inside it, rounded to 4 decimals. */
export function pointFromClick(clientX: number, clientY: number, box: { left: number; top: number; width: number; height: number }): [number, number] {
  const x = box.width > 0 ? (clientX - box.left) / box.width : 0;
  const y = box.height > 0 ? (clientY - box.top) / box.height : 0;
  return [Math.round(clamp01(x) * 1e4) / 1e4, Math.round(clamp01(y) * 1e4) / 1e4];
}

/** A rectangle in the middle of the picture to start from (4 corners, clockwise from the top left). */
export const defaultRectangle = (kind: ZoneKind): ZoneDraft => ({
  kind,
  points: [
    [0.3, 0.3],
    [0.7, 0.3],
    [0.7, 0.7],
    [0.3, 0.7],
  ],
});

/** Twice the signed area, to tell a real polygon from three points in a row. */
export function polygonArea(points: [number, number][]): number {
  let a = 0;
  for (let i = 0; i < points.length; i++) {
    const [x1, y1] = points[i];
    const [x2, y2] = points[(i + 1) % points.length];
    a += x1 * y2 - x2 * y1;
  }
  return Math.abs(a) / 2;
}

/** What is wrong with a list of zones (empty = fine). */
export function zoneErrors(zones: ZoneDraft[]): string[] {
  const out: string[] = [];
  if (zones.length > MAX_ZONES) out.push(`At most ${MAX_ZONES} zones per camera.`);
  zones.forEach((z, i) => {
    if (z.points.length < MIN_POINTS || z.points.length > MAX_POINTS) out.push(`Zone ${i + 1} needs ${MIN_POINTS} to ${MAX_POINTS} points.`);
    else if (polygonArea(z.points) < 1e-4) out.push(`Zone ${i + 1} has no area (its points are in a line).`);
  });
  return out;
}

export const zonesOf = (zones: Zone[]): ZoneDraft[] => zones.map((z) => ({ kind: z.kind, zone_id: z.zone_id, points: z.points.map(([x, y]) => [x, y]) }));

/** Same zones (kind, number and points), whatever the object identities. */
export const sameZones = (a: ZoneDraft[], b: ZoneDraft[]): boolean => JSON.stringify(a) === JSON.stringify(b);

/** SVG `points` attribute for a viewBox of 0 0 1 1. */
export const svgPoints = (points: [number, number][]): string => points.map(([x, y]) => `${x},${y}`).join(" ");
