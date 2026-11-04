import type { EngineSummary } from "../types";
import type { Tone } from "./health";

export interface EngineNote {
  tone: Tone;
  /** One line: what state the engine is in. */
  headline: string;
  /** What the next camera will do, or why the engine cannot be reached; null when there is nothing to add. */
  detail: string | null;
  /** "3 of 8", or null when the number of places is not known. */
  places: string | null;
}

/**
 * The engine as the Cameras page tells it. The engine has a number of places; a camera that fits is added
 * or removed without interrupting any other, one that does not fit restarts the engine once.
 */
export function describeEngine(e: EngineSummary | undefined): EngineNote | null {
  if (!e) return null;
  if (e.manager === "not_configured") return null;   // this backend does not run an engine: nothing to show
  if (e.manager === "unreachable") {
    return { tone: "bad", headline: "Engine manager not reachable", detail: "Cameras cannot be started or stopped until it is back.", places: null };
  }
  const places = e.capacity ? `${Math.min(e.wanted, e.capacity)} of ${e.capacity}` : null;
  const headline = e.state === "running" ? "Engine running" : e.state ? `Engine ${e.state}` : "Engine";
  let detail: string | null = null;
  let tone: Tone = e.state === "running" ? "ok" : "idle";
  if (e.applied === false) {
    detail = "Applying the latest change...";
  } else if (e.capacity && e.room !== null && e.room !== undefined) {
    if (e.room === 0) {
      tone = e.state === "running" ? "warn" : tone;
      detail = "All places are in use: the next camera restarts the engine, and every camera loses its picture for a few seconds.";
    } else {
      detail = `${e.room} ${e.room === 1 ? "place" : "places"} free: a new camera starts without interrupting the others.`;
    }
  }
  return { tone, headline, detail, places };
}
