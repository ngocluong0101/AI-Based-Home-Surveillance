import type { Tone } from "../../utils/health";

/** A coloured dot; `pulse` adds the ring that says "this is live". */
export function StatusDot({ tone, pulse = false }: { tone: Tone; pulse?: boolean }) {
  const colour = tone === "idle" ? "" : `dot-${tone}`;
  return <span className={`dot ${colour} ${pulse && tone !== "idle" ? "dot-pulse" : ""}`} aria-hidden="true" />;
}
