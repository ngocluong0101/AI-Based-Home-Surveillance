import type { PersonEvent } from "../types";
import { percent, relativeTime, timeOfDay } from "../utils/format";
import { CoverImage } from "./CoverImage";

/** One event as a picture card (the gallery on the Events page). The text sits under the picture so it is always readable. */
export function EventCard({
  event,
  cameraName,
  now,
  highlighted,
  onOpen,
}: {
  event: PersonEvent;
  cameraName: string;
  now: number;
  highlighted: boolean;
  onOpen: (id: string) => void;
}) {
  return (
    <button className={`shot ${highlighted ? "is-new" : ""}`} onClick={() => onOpen(event.event_id)}>
      <span className="shot-img">
        <CoverImage src={event.snapshot_url} />
        {event.status === "new" && <span className="shot-new">New</span>}
        <span className="shot-conf">{percent(event.confidence)}</span>
      </span>
      <span className="shot-cap">
        <b>
          <span className="sr-only">Person detected, </span>
          {cameraName}
        </b>
        <span>
          {timeOfDay(event.timestamp)} · {relativeTime(event.timestamp, now)}
        </span>
      </span>
    </button>
  );
}
