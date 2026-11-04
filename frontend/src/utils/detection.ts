import type { Camera } from "../types";

/** "10 s", "1 min 30 s", "2 h": a duration in seconds as a person reads it. */
export function formatSeconds(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return `${total} s`;
  if (total < 3600) {
    const m = Math.floor(total / 60);
    const s = total % 60;
    return s === 0 ? `${m} min` : `${m} min ${s} s`;
  }
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  return m === 0 ? `${h} h` : `${h} h ${m} min`;
}

/** The detector never reports a person below this confidence, whatever a camera asks for (engine/configs). */
export const DETECTOR_FLOOR = 0.25;

export interface DetectionSummary {
  /** AI off: the picture goes on, nobody is detected. */
  aiOff: boolean;
  /** "25%" and up, or null when the camera has no filter of its own (it keeps what the detector gives). */
  minConfidence: string | null;
  /** A camera with a stricter threshold than the detector's own floor. */
  stricter: boolean;
  /** How long a person must be out of sight before the same person raises a new alert. */
  repeatAfter: string;
}

/** What a camera's detection settings mean, for the cameras table. */
export function describeDetection(camera: Pick<Camera, "ai_enabled" | "conf_threshold" | "absence_timeout">): DetectionSummary {
  const stricter = camera.conf_threshold > DETECTOR_FLOOR + 1e-9;
  const shown = Math.max(camera.conf_threshold, DETECTOR_FLOOR);
  return {
    aiOff: !camera.ai_enabled,
    minConfidence: `${Math.round(shown * 100)}%`,
    stricter,
    repeatAfter: formatSeconds(camera.absence_timeout),
  };
}
