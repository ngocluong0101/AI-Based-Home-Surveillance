/*
 * @author: Ngoc Luong <ngocluongg0101@gmail.com>
 */

import type { PersonEvent } from "../types";
import { isAlert, kindOf, verificationNote } from "../utils/eventType";
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
  const kind = kindOf(event.type);
  const Icon = kind.icon;
  const check = verificationNote(event);
  return (
    <button className={`shot ${highlighted ? "is-new" : ""} ${isAlert(event) ? "is-alert" : ""}`} onClick={() => onOpen(event.event_id)}>
      <span className="shot-img">
        <CoverImage src={event.snapshot_url} />
        {event.status === "new" && <span className="shot-new">New</span>}
        {event.type !== "PERSON_DETECTED" && (
          <span className={`shot-kind shot-kind-${event.severity}`}>
            <Icon size={13} aria-hidden /> {kind.short}
          </span>
        )}
        {check && <span className={`shot-check shot-check-${check.tone}`}>{check.label}</span>}
        <span className="shot-conf">{percent(event.confidence)}</span>
      </span>
      <span className="shot-cap">
        <b>
          <span className="sr-only">{kind.label}, </span>
          {cameraName}
        </b>
        <span>
          {timeOfDay(event.timestamp)} · {relativeTime(event.timestamp, now)}
        </span>
      </span>
    </button>
  );
}
