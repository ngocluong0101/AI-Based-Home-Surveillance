import { Gauge, Users } from "lucide-react";
import type { StreamInfo } from "../types";

/** What the camera is doing right now: people in view and frames per second. Nothing when the engine cannot tell. */
export function LiveChips({ stream }: { stream: StreamInfo | undefined }) {
  const reporting = stream?.reporting === true;
  const people = reporting && stream.people != null ? stream.people : null;
  const fps = reporting && stream.fps !== null ? stream.fps : null;
  if (people === null && fps === null) return null;
  return (
    <span className="chips">
      {people !== null && (
        <span className={`chip ${people > 0 ? "chip-warn" : ""}`}>
          <Users size={13} aria-hidden="true" />
          <span className="sr-only">People now: </span>
          {people}
        </span>
      )}
      {fps !== null && (
        <span className="chip">
          <Gauge size={13} aria-hidden="true" />
          {fps.toFixed(0)} fps
        </span>
      )}
    </span>
  );
}
