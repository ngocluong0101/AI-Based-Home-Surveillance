/*
 * @author: Ngoc Luong <ngocluongg0101@gmail.com>
 */

// The made-up world of the demo: rooms, and people who walk through them. Everything is a function of (camera, time), so the
// live picture, the "people now" count and the snapshots of past events all agree, and nothing has to be stored.

/** A person's box, relative to the frame (x, y = top-left corner). */
export interface DemoPerson {
  id: number;
  x: number;
  y: number;
  w: number;
  h: number;
  /** 0 = on their feet ... 1 = on the floor. Absent = standing. */
  lying?: number;
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
  if (cameraId === FALL_CAMERA) {
    const faller = fallerAt(tMs);
    if (faller) out.push(faller);
  }
  return out;
}

// ---- the person who falls (on the camera that has the fall rule)

/** The living room: its camera has the fall rule in the demo's seed data. */
export const FALL_CAMERA = 3;
const FALL_PERIOD_S = 70;
const WALK_IN_S = 8; // from the start of a cycle
const FALL_S = 0.9; // how long the fall itself takes
const LIE_UNTIL_S = 20;
const GET_UP_S = 2.5;
const WALK_OUT_UNTIL_S = 30;
/** The alert comes this long after the fall began: the rules wait to see the person stay down. */
export const FALL_ALERT_AFTER_S = WALK_IN_S + FALL_S + 1.1;
const FALLER_FOOT = 0.88;
const FALLER_HEIGHT = 0.4;

const smooth = (u: number): number => u * u * (3 - 2 * u);

/** The person who walks in, falls, lies for a while, gets up and leaves; null when nobody is there. Starts every FALL_PERIOD_S. */
export function fallerAt(tMs: number): DemoPerson | null {
  const period = FALL_PERIOD_S * 1000;
  const cycle = Math.floor(tMs / period);
  const t = (tMs - cycle * period) / 1000;
  if (t >= WALK_OUT_UNTIL_S) return null;
  let cx: number;
  let lying = 0;
  if (t < WALK_IN_S) {
    cx = 0.08 + 0.42 * (t / WALK_IN_S);
  } else if (t < WALK_IN_S + FALL_S) {
    const u = (t - WALK_IN_S) / FALL_S;
    cx = 0.5 + 0.04 * u;
    lying = smooth(u);
  } else if (t < LIE_UNTIL_S) {
    cx = 0.54;
    lying = 1;
  } else if (t < LIE_UNTIL_S + GET_UP_S) {
    const u = (t - LIE_UNTIL_S) / GET_UP_S;
    cx = 0.54 - 0.04 * u;
    lying = 1 - smooth(u);
  } else {
    cx = 0.5 + 0.42 * ((t - LIE_UNTIL_S - GET_UP_S) / (WALK_OUT_UNTIL_S - LIE_UNTIL_S - GET_UP_S));
  }
  const standW = (0.38 * FALLER_HEIGHT * FRAME_H) / FRAME_W;
  const lieW = (0.95 * FALLER_HEIGHT * FRAME_H) / FRAME_W;
  const w = standW + (lieW - standW) * lying;
  const h = FALLER_HEIGHT + (0.22 * FALLER_HEIGHT - FALLER_HEIGHT) * lying;
  return { id: 900 + (cycle % 97), x: Math.min(Math.max(cx - w / 2, 0), 1 - w), y: FALLER_FOOT - h, w, h, lying };
}

/** When the next fall alert of the demo is due (ms since the epoch), strictly after `nowMs`. */
export function nextFallAlertMs(nowMs: number): number {
  const period = FALL_PERIOD_S * 1000;
  const offset = FALL_ALERT_AFTER_S * 1000;
  return (Math.floor((nowMs - offset) / period) + 1) * period + offset;
}

/** The person as they are lying on the floor, for the picture of a fall that happened at `tMs` (past events too). */
export function lyingPersonAt(tMs: number): DemoPerson {
  const period = FALL_PERIOD_S * 1000;
  const cycleStart = Math.floor(tMs / period) * period;
  return fallerAt(cycleStart + (WALK_IN_S + FALL_S + 3) * 1000) as DemoPerson;
}

// ---- the skeleton, drawn like the engine does

type Pt = [number, number];
const NAMES = ["nose", "ls", "rs", "le", "re", "lw", "rw", "lh", "rh", "lk", "rk", "la", "ra"] as const;
export type JointName = (typeof NAMES)[number];

// Joint positions inside the person's box (0..1 in both directions), standing and lying on the floor (head to the right).
const STANDING: Record<JointName, Pt> = {
  nose: [0.5, 0.07], ls: [0.32, 0.2], rs: [0.68, 0.2], le: [0.24, 0.37], re: [0.76, 0.37], lw: [0.22, 0.52], rw: [0.78, 0.52],
  lh: [0.4, 0.55], rh: [0.6, 0.55], lk: [0.4, 0.76], rk: [0.6, 0.76], la: [0.4, 0.97], ra: [0.6, 0.97],
};
const LYING: Record<JointName, Pt> = {
  nose: [0.94, 0.5], ls: [0.78, 0.3], rs: [0.78, 0.72], le: [0.66, 0.12], re: [0.66, 0.9], lw: [0.54, 0.22], rw: [0.54, 0.82],
  lh: [0.42, 0.36], rh: [0.42, 0.66], lk: [0.22, 0.3], rk: [0.22, 0.7], la: [0.05, 0.4], ra: [0.05, 0.6],
};
export const BONES: [JointName, JointName][] = [
  ["nose", "ls"], ["nose", "rs"], ["ls", "rs"], ["ls", "le"], ["le", "lw"], ["rs", "re"], ["re", "rw"], ["ls", "lh"], ["rs", "rh"],
  ["lh", "rh"], ["lh", "lk"], ["lk", "la"], ["rh", "rk"], ["rk", "ra"],
];

/** Where the joints of `p` are, in pixels of the frame. Standing people swing their arms and legs while they walk. */
export function jointsOf(p: DemoPerson, tMs: number): Record<JointName, Pt> {
  const e = p.lying ?? 0;
  const swing = Math.sin(tMs / 1000 * 6 + p.id) * (1 - e);
  const out = {} as Record<JointName, Pt>;
  for (const name of NAMES) {
    let [u, v] = [STANDING[name][0] + (LYING[name][0] - STANDING[name][0]) * e, STANDING[name][1] + (LYING[name][1] - STANDING[name][1]) * e];
    const left = name.startsWith("l");
    const sign = left ? 1 : -1;
    if (name === "la" || name === "ra") u += 0.14 * swing * sign;
    else if (name === "lk" || name === "rk") u += 0.07 * swing * sign;
    else if (name === "lw" || name === "rw") u -= 0.1 * swing * sign;
    out[name] = [(p.x + u * p.w) * FRAME_W, (p.y + v * p.h) * FRAME_H];
  }
  return out;
}

export interface Figure {
  /** Thick strokes in the colour of the clothes: [x1, y1, x2, y2, width]. */
  limbs: [number, number, number, number, number][];
  head: [number, number, number]; // centre x, centre y, radius
  /** The skeleton the engine draws on top. */
  bones: [number, number, number, number][];
  dots: Pt[];
}

/** The person as strokes: shared by the canvas of the live picture and the SVG of the snapshots. */
export function figureOf(p: DemoPerson, tMs: number): Figure {
  const j = jointsOf(p, tMs);
  const size = Math.max(p.h * FRAME_H, (p.w * FRAME_W) / 0.95);
  const mid = (a: Pt, b: Pt): Pt => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  const sh = mid(j.ls, j.rs);
  const hip = mid(j.lh, j.rh);
  const thin = size * 0.065;
  const limbs: Figure["limbs"] = [
    [sh[0], sh[1], hip[0], hip[1], size * 0.2],
    [j.ls[0], j.ls[1], j.le[0], j.le[1], thin], [j.le[0], j.le[1], j.lw[0], j.lw[1], thin],
    [j.rs[0], j.rs[1], j.re[0], j.re[1], thin], [j.re[0], j.re[1], j.rw[0], j.rw[1], thin],
    [j.lh[0], j.lh[1], j.lk[0], j.lk[1], thin * 1.2], [j.lk[0], j.lk[1], j.la[0], j.la[1], thin * 1.2],
    [j.rh[0], j.rh[1], j.rk[0], j.rk[1], thin * 1.2], [j.rk[0], j.rk[1], j.ra[0], j.ra[1], thin * 1.2],
  ];
  return {
    limbs,
    head: [j.nose[0], j.nose[1], size * 0.07],
    bones: BONES.map(([a, b]) => [j[a][0], j[a][1], j[b][0], j[b][1]]),
    dots: NAMES.map((n) => j[n]),
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
    const f = figureOf(p, tMs);
    const colour = clothesOf(p.id);
    for (const [x1, y1, x2, y2, w] of f.limbs) {
      parts.push(`<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" stroke="${colour}" stroke-width="${w.toFixed(1)}" stroke-linecap="round"/>`);
    }
    parts.push(`<circle cx="${f.head[0].toFixed(1)}" cy="${f.head[1].toFixed(1)}" r="${f.head[2].toFixed(1)}" fill="#e2c9b0"/>`);
    for (const [x1, y1, x2, y2] of f.bones) {
      parts.push(`<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" stroke="#7cf0c9" stroke-width="2"/>`);
    }
    for (const [x, y] of f.dots) parts.push(`<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3" fill="#ff5d73"/>`);
  }
  const label = `${cameraName.replace(/[<>&"]/g, "")}  ${clockText(tMs)}`;
  parts.push(`<text x="12" y="24" font-family="monospace" font-size="16" fill="#e6edf7" fill-opacity="0.85">${label}</text>`);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}">${parts.join("")}</svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}
