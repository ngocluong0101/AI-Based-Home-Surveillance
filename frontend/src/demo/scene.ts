// The made-up world of the demo: rooms, and people who walk through them. Everything is a function of (camera, time), so the
// live picture, the "people now" count and the snapshots of past events all agree, and nothing has to be stored.

/** A person's box, relative to the frame (x, y = top-left corner). */
export interface DemoPerson {
  id: number;
  x: number;
  y: number;
  w: number;
  h: number;
}

export const FRAME_W = 640;
export const FRAME_H = 360;

export interface Theme {
  wall: string;
  floor: string;
  /** Things on the wall and floor: [x, y, w, h, fill], all relative to the frame. */
  rects: [number, number, number, number, string][];
}

const THEMES: Theme[] = [
  // front door: a door, a side window and a mat
  { wall: "#2a3446", floor: "#1b212e", rects: [[0.62, 0.1, 0.16, 0.56, "#3d4c66"], [0.1, 0.18, 0.18, 0.26, "#506485"], [0.55, 0.8, 0.3, 0.06, "#252d3d"]] },
  // garage: a roller door and shelves
  { wall: "#303034", floor: "#202023", rects: [[0.18, 0.08, 0.5, 0.56, "#46464c"], [0.78, 0.15, 0.16, 0.45, "#3a3a40"], [0.06, 0.62, 0.1, 0.1, "#2b2b30"]] },
  // living room: a window and a sofa
  { wall: "#2d3a3a", floor: "#1c2626", rects: [[0.12, 0.12, 0.3, 0.34, "#4f6f78"], [0.58, 0.46, 0.34, 0.2, "#3a4f4f"], [0.3, 0.84, 0.4, 0.06, "#233030"]] },
  // backyard: a fence and a tree trunk
  { wall: "#25362c", floor: "#1a261e", rects: [[0, 0.3, 1, 0.3, "#314a3a"], [0.8, 0.05, 0.05, 0.6, "#4a3a2c"], [0.2, 0.2, 0.04, 0.4, "#3a5545"]] },
  // shed
  { wall: "#363028", floor: "#231f1a", rects: [[0.15, 0.1, 0.3, 0.5, "#4d4337"], [0.6, 0.2, 0.25, 0.3, "#403830"]] },
];

export const themeOf = (cameraId: number): Theme => THEMES[(cameraId - 1 + THEMES.length) % THEMES.length];

/** Horizon: where the wall meets the floor, relative to the frame height. */
export const HORIZON = 0.64;

const CLOTHES = ["#c97b63", "#6f9bd1", "#8cc084", "#d1b36f", "#a98bd1"];
export const clothesOf = (personId: number): string => CLOTHES[personId % CLOTHES.length];

/** Three walkers per camera, each crossing the picture now and then with a different rhythm. */
const SLOTS = 3;
const PERIOD_S = [34, 47, 61];
const IN_VIEW = 0.72; // share of each cycle a walker is in view

/** Who is in front of camera `cameraId` at time `tMs` (ms since the epoch). The id is a track id: one per crossing. */
export function peopleAt(cameraId: number, tMs: number): DemoPerson[] {
  const out: DemoPerson[] = [];
  for (let i = 0; i < SLOTS; i++) {
    const period = (PERIOD_S[i] + cameraId * 3) * 1000;
    const u = tMs / period + ((cameraId * 7919 + i * 104729) % 1000) / 1000;
    const cycle = Math.floor(u);
    const p = u - cycle;
    if (p >= IN_VIEW) continue;
    const progress = p / IN_VIEW;
    const goingRight = (cycle + i) % 2 === 0;
    const centre = 0.08 + 0.84 * (goingRight ? progress : 1 - progress);
    const foot = 0.76 + 0.17 * (((i * 37 + cameraId * 11) % 10) / 10); // nearer the camera = lower and bigger
    const h = 0.3 + (foot - 0.72) * 1.2;
    const w = (0.38 * h * FRAME_H) / FRAME_W;
    out.push({ id: (cycle % 997) * SLOTS + i + 1, x: Math.min(Math.max(centre - w / 2, 0), 1 - w), y: foot - h, w, h });
  }
  return out;
}

/** The look of a person inside a box of pixels, as pieces. Shared by the canvas and the SVG drawing. */
export function personParts(px: { x: number; y: number; w: number; h: number }): {
  head: [number, number, number]; // centre x, centre y, radius
  rects: [number, number, number, number, number][]; // x, y, w, h, corner radius
} {
  const { x, y, w, h } = px;
  return {
    head: [x + w / 2, y + 0.09 * h, 0.075 * h],
    rects: [
      [x + 0.05 * w, y + 0.18 * h, 0.9 * w, 0.4 * h, 0.12 * w], // body
      [x + 0.1 * w, y + 0.57 * h, 0.36 * w, 0.43 * h, 0.06 * w], // legs
      [x + 0.54 * w, y + 0.57 * h, 0.36 * w, 0.43 * h, 0.06 * w],
    ],
  };
}

export function clockText(tMs: number): string {
  const d = new Date(tMs);
  const two = (n: number) => String(n).padStart(2, "0");
  return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`;
}

/** A snapshot as an image address: the room, the camera's label and the given people (no box: the app draws it). */
export function snapshotUrl(cameraId: number, cameraName: string, people: DemoPerson[], tMs: number): string {
  const t = themeOf(cameraId);
  const W = FRAME_W;
  const H = FRAME_H;
  const parts: string[] = [
    `<rect width="${W}" height="${H}" fill="${t.wall}"/>`,
    `<rect y="${HORIZON * H}" width="${W}" height="${H - HORIZON * H}" fill="${t.floor}"/>`,
    ...t.rects.map(([x, y, w, h, fill]) => `<rect x="${x * W}" y="${y * H}" width="${w * W}" height="${h * H}" fill="${fill}"/>`),
  ];
  for (const p of people) {
    const { head, rects } = personParts({ x: p.x * W, y: p.y * H, w: p.w * W, h: p.h * H });
    const colour = clothesOf(p.id);
    for (const [x, y, w, h, r] of rects) {
      parts.push(`<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${w.toFixed(1)}" height="${h.toFixed(1)}" rx="${r.toFixed(1)}" fill="${colour}"/>`);
    }
    parts.push(`<circle cx="${head[0].toFixed(1)}" cy="${head[1].toFixed(1)}" r="${head[2].toFixed(1)}" fill="#e2c9b0"/>`);
  }
  const label = `${cameraName.replace(/[<>&"]/g, "")}  ${clockText(tMs)}`;
  parts.push(`<text x="12" y="24" font-family="monospace" font-size="16" fill="#e6edf7" fill-opacity="0.85">${label}</text>`);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}">${parts.join("")}</svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}
