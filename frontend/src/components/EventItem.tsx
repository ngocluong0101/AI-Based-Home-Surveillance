import { UserRound } from "lucide-react";
import type { PersonEvent } from "../types";
import { percent, relativeTime } from "../utils/format";
import { Badge } from "./ui/Badge";
import { Thumb } from "./Thumb";

/** One event as a compact row (dashboard, monitoring side list). */
export function EventItem({
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
    <button className={`event-item ${highlighted ? "is-new" : ""}`} onClick={() => onOpen(event.event_id)}>
      <Thumb src={event.snapshot_url} />
      <span className="grow">
        <span className="title">
          <UserRound size={14} /> Person detected
          {event.status === "new" && <Badge tone="warn">New</Badge>}
        </span>
        <span className="sub" style={{ display: "block" }}>
          {cameraName} · track {event.track_id} · {percent(event.confidence)} · {relativeTime(event.timestamp, now)}
        </span>
      </span>
    </button>
  );
}
