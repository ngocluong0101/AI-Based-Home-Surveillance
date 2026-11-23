/*
 * @author: Ngoc Luong <ngocluongg0101@gmail.com>
 */

import { Plus, Trash2 } from "lucide-react";
import { useRef, useState } from "react";
import {
  defaultRectangle,
  MAX_POINTS,
  MAX_ZONES,
  pointFromClick,
  svgPoints,
  ZONE_COLOR,
  ZONE_LABEL,
  type ZoneDraft,
} from "../utils/zones";
import type { ZoneKind } from "../types";

/**
 * Draw the zones of a camera on its picture. "Add" puts a rectangle in the middle; drag a corner (or use the arrow keys) to move it;
 * "Add point" / "Remove point" change the number of corners. Coordinates are relative to the frame. `background` is a snapshot of the
 * camera, when there is one.
 */
export function ZoneEditor({
  zones,
  onChange,
  background,
}: {
  zones: ZoneDraft[];
  onChange: (zones: ZoneDraft[]) => void;
  background: string | null;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<{ zone: number; point: number } | null>(null);
  const [selected, setSelected] = useState<number | null>(null);

  const move = (clientX: number, clientY: number) => {
    if (!drag || !box.current) return;
    const p = pointFromClick(clientX, clientY, box.current.getBoundingClientRect());
    onChange(zones.map((z, i) => (i === drag.zone ? { ...z, points: z.points.map((q, k) => (k === drag.point ? p : q)) } : z)));
  };

  const add = (kind: ZoneKind) => {
    if (zones.length >= MAX_ZONES) return;
    onChange([...zones, defaultRectangle(kind)]);
    setSelected(zones.length);
  };
  const remove = (i: number) => {
    onChange(zones.filter((_, k) => k !== i));
    setSelected(null);
  };
  const setKind = (i: number, kind: ZoneKind) => onChange(zones.map((z, k) => (k === i ? { ...z, kind } : z)));
  const addPoint = (i: number) => {
    const z = zones[i];
    if (z.points.length >= MAX_POINTS) return;
    // a new point halfway between the last two: it can then be dragged where it belongs
    const [x1, y1] = z.points[z.points.length - 1];
    const [x2, y2] = z.points[0];
    onChange(zones.map((q, k) => (k === i ? { ...q, points: [...q.points, [(x1 + x2) / 2, (y1 + y2) / 2]] } : q)));
  };
  const removePoint = (i: number) => {
    const z = zones[i];
    if (z.points.length <= 3) return;
    onChange(zones.map((q, k) => (k === i ? { ...q, points: q.points.slice(0, -1) } : q)));
  };

  return (
    <div className="zone-editor">
      <div
        ref={box}
        className="zone-canvas"
        style={background ? { backgroundImage: `url(${background})` } : undefined}
        onPointerMove={(e) => move(e.clientX, e.clientY)}
        onPointerUp={() => setDrag(null)}
        onPointerLeave={() => setDrag(null)}
      >
        <svg viewBox="0 0 1 1" preserveAspectRatio="none" aria-label="Zones drawn on the camera picture" role="img">
          {zones.map((z, i) => (
            <polygon
              key={i}
              points={svgPoints(z.points)}
              fill={ZONE_COLOR[z.kind]}
              fillOpacity={selected === i ? 0.3 : 0.18}
              stroke={ZONE_COLOR[z.kind]}
              strokeWidth={selected === i ? 3 : 2}
              vectorEffect="non-scaling-stroke"
            />
          ))}
        </svg>
        {zones.map((z, i) =>
          z.points.map(([x, y], k) => (
            <button
              type="button"
              key={`${i}-${k}`}
              className="zone-handle"
              style={{ left: `${x * 100}%`, top: `${y * 100}%`, background: ZONE_COLOR[z.kind] }}
              aria-label={`Zone ${i + 1}, corner ${k + 1}: drag, or use the arrow keys`}
              onPointerDown={(e) => {
                e.preventDefault();
                setSelected(i);
                setDrag({ zone: i, point: k });
              }}
              onKeyDown={(e) => {
                const step = e.shiftKey ? 0.05 : 0.01;
                const d: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
                const delta = d[e.key];
                if (!delta) return;
                e.preventDefault();
                const clamp = (v: number) => Math.round(Math.min(1, Math.max(0, v)) * 1e4) / 1e4;
                onChange(zones.map((q, a) => (a === i ? { ...q, points: q.points.map((p, b) => (b === k ? [clamp(p[0] + delta[0]), clamp(p[1] + delta[1])] : p)) } : q)));
              }}
            />
          )),
        )}
      </div>

      <div className="zone-add">
        {(Object.keys(ZONE_LABEL) as ZoneKind[]).map((kind) => (
          <button key={kind} type="button" className="btn btn-sm" disabled={zones.length >= MAX_ZONES} onClick={() => add(kind)}>
            <Plus size={14} aria-hidden /> {ZONE_LABEL[kind]}
          </button>
        ))}
      </div>

      {zones.length > 0 && (
        <ul className="zone-list">
          {zones.map((z, i) => (
            <li key={i} className={selected === i ? "is-selected" : undefined}>
              <span className="swatch" style={{ background: ZONE_COLOR[z.kind] }} aria-hidden />
              <label className="field zone-kind">
                <span className="sr-only">Type of zone {i + 1}</span>
                <select value={z.kind} onChange={(e) => setKind(i, e.target.value as ZoneKind)}>
                  {(Object.keys(ZONE_LABEL) as ZoneKind[]).map((k) => (
                    <option key={k} value={k}>{ZONE_LABEL[k]}</option>
                  ))}
                </select>
              </label>
              <span className="muted">{z.points.length} points</span>
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => addPoint(i)} disabled={z.points.length >= MAX_POINTS}>
                Add point
              </button>
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => removePoint(i)} disabled={z.points.length <= 3}>
                Remove point
              </button>
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => remove(i)} aria-label={`Delete zone ${i + 1}`}>
                <Trash2 size={14} aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
