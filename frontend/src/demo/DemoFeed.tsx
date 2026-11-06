import { useEffect, useRef } from "react";
import { clockText, clothesOf, FRAME_H, FRAME_W, HORIZON, peopleAt, personParts, themeOf } from "./scene";

const FRAME_MS = 50; // 20 pictures a second is plenty for a made-up room

/**
 * The picture of a camera in the demo: a drawn room with people walking through it, each in a box with a track id, like the
 * engine's own video. Takes the place of the WebRTC video, so the demo needs no media server.
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
        const { head, rects } = personParts({ x: p.x * W, y: p.y * H, w: p.w * W, h: p.h * H });
        ctx.fillStyle = clothesOf(p.id);
        for (const [x, y, w, h, r] of rects) {
          ctx.beginPath();
          ctx.roundRect(x, y, w, h, r);
          ctx.fill();
        }
        ctx.fillStyle = "#e2c9b0";
        ctx.beginPath();
        ctx.arc(head[0], head[1], head[2], 0, Math.PI * 2);
        ctx.fill();
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
