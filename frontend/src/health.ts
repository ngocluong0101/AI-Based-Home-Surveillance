import type { CameraStatus, StreamInfo } from "./api";

export type Tone = "ok" | "warn" | "bad" | "idle";

export interface Health {
  label: string;
  tone: Tone;
}

const NO_FRAMES_AFTER_S = 5;

/** The stream view to show: the pushed heartbeat when there is one, else what the last poll saw. */
export function streamOf(status: CameraStatus | undefined, live: StreamInfo | undefined): StreamInfo | undefined {
  return live ?? status?.stream;
}

export function healthOf(status: CameraStatus | undefined, stream: StreamInfo | undefined): Health {
  if (stream?.reporting) {
    if (stream.state === "RECONNECTING") return { label: "Reconnecting", tone: "warn" };
    if (stream.fps === 0 && (stream.last_frame_age ?? 0) > NO_FRAMES_AFTER_S) return { label: "No frames", tone: "warn" };
    return { label: "Online", tone: "ok" };
  }
  if (!status) return { label: "Unknown", tone: "idle" };
  if (!status.should_run) return { label: "Stopped", tone: "idle" };
  if (status.engine.manager === "unreachable") return { label: "Engine manager down", tone: "bad" };
  if (status.engine.state === "backoff") return { label: "Engine crashed", tone: "bad" };
  return { label: "Starting", tone: "warn" };
}

/** A sentence for the "last error" line, or null when nothing is wrong. */
export function lastErrorOf(status: CameraStatus | undefined, stream: StreamInfo | undefined): string | null {
  if (!status) return null;
  const { engine } = status;
  if (engine.manager === "unreachable") return "The engine manager cannot be reached.";
  if (engine.state === "backoff") {
    return `The engine exited${engine.last_exit_code !== null ? ` (code ${engine.last_exit_code})` : ""} and is being restarted.`;
  }
  if (stream?.reporting && stream.state === "RECONNECTING") {
    return `No frames from the camera for ${Math.round(stream.last_frame_age ?? 0)} s; reconnecting.`;
  }
  if (engine.last_exit_code !== null && engine.last_exit_code !== 0 && !stream?.reporting) {
    return `The engine last exited with code ${engine.last_exit_code}.`;
  }
  return null;
}
