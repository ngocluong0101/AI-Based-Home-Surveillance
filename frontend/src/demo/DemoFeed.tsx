/*
 * @author: Ngoc Luong <ngocluongg0101@gmail.com>
 */

import { useEffect, useRef } from "react";
import { clockText, clothesOf, figureOf, FRAME_H, FRAME_W, HORIZON, peopleAt, themeOf } from "./scene";

const FRAME_MS = 50; // 20 pictures a second is plenty for a made-up room

/**
 * The picture of a camera in the demo: a drawn room with people walking through it (and, in the living room, now and then falling),
 * each with a skeleton and a box with a track id, like the engine's own video. Takes the place of the WebRTC video, so the demo needs no media server.
 */
export default function DemoFeed({ cameraId, cameraName }: { cameraId: number; cameraName: string }) {
  const canvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const ctx = canvas.current?.getContext("2d");
    if (!ctx) return;
    const theme = themeOf(cameraId);
    let raf = 0;
    let last = 0;

    const draw = (now: number) => {
      raf = requestAnimationFrame(draw);
      if (now - last < FRAME_MS) return;
      last = now;
      const t = Date.now();
      const W = FRAME_W;
      const H = FRAME_H;
      ctx.fillStyle = theme.wall;
      ctx.fillRect(0, 0, W, H);
      ctx.fillStyle = theme.floor;
      ctx.fillRect(0, HORIZON * H, W, H - HORIZON * H);
      for (const [x, y, w, h, fill] of theme.rects) {
        ctx.fillStyle = fill;
        ctx.fillRect(x * W, y * H, w * W, h * H);
      }
      ctx.font = "bold 12px sans-serif";
      for (const p of peopleAt(cameraId, t)) {
        const f = figureOf(p, t);
        ctx.lineCap = "round";
        ctx.strokeStyle = clothesOf(p.id);
        for (const [x1, y1, x2, y2, width] of f.limbs) {
          ctx.lineWidth = width;
          ctx.beginPath();
          ctx.moveTo(x1, y1);
          ctx.lineTo(x2, y2);
          ctx.stroke();
        }
        ctx.fillStyle = "#e2c9b0";
        ctx.beginPath();
        ctx.arc(f.head[0], f.head[1], f.head[2], 0, Math.PI * 2);
        ctx.fill();
        // the skeleton the engine draws: bones and joints
        ctx.strokeStyle = "#7cf0c9";
        ctx.lineWidth = 2;
        for (const [x1, y1, x2, y2] of f.bones) {
          ctx.beginPath();
          ctx.moveTo(x1, y1);
          ctx.lineTo(x2, y2);
          ctx.stroke();
        }
        ctx.fillStyle = "#ff5d73";
        for (const [x, y] of f.dots) {
          ctx.beginPath();
          ctx.arc(x, y, 3, 0, Math.PI * 2);
          ctx.fill();
        }
        // the box and track id the engine draws
        ctx.strokeStyle = "#3ddc97";
        ctx.lineWidth = 2;
        ctx.strokeRect(p.x * W, p.y * H, p.w * W, p.h * H);
        const label = `person ${p.id}`;
        const width = ctx.measureText(label).width + 8;
        ctx.fillStyle = "#3ddc97";
        ctx.fillRect(p.x * W, p.y * H - 16, width, 16);
        ctx.fillStyle = "#04101f";
        ctx.fillText(label, p.x * W + 4, p.y * H - 4);
      }
      // bottom right: the LIVE tag, the fullscreen button and the chips of the page take the other corners
      ctx.font = "16px monospace";
      ctx.fillStyle = "rgba(230, 237, 247, 0.85)";
      ctx.textAlign = "right";
      ctx.fillText(`${cameraName}  ${clockText(t)}`, W - 12, H - 14);
      ctx.textAlign = "left";
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [cameraId, cameraName]);

  return <canvas ref={canvas} width={FRAME_W} height={FRAME_H} role="img" aria-label={`Simulated live picture of ${cameraName}`} />;
}
