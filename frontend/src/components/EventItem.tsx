import type { PersonEvent } from "../types";
import { describeEvent, isAlert, kindOf, SEVERITY_LABEL, SEVERITY_TONE, verificationNote } from "../utils/eventType";
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
  const kind = kindOf(event.type);
  const Icon = kind.icon;
  const check = verificationNote(event);
  return (
    <button className={`event-item ${highlighted ? "is-new" : ""} ${isAlert(event) ? "is-alert" : ""}`} onClick={() => onOpen(event.event_id)}>
      <Thumb src={event.snapshot_url} />
      <span className="grow">
        <span className="title">
          <Icon size={14} /> {kind.label}
          {event.severity !== "info" && <Badge tone={SEVERITY_TONE[event.severity]}>{SEVERITY_LABEL[event.severity]}</Badge>}
          {check && <Badge tone={check.tone}>{check.label}</Badge>}
          {event.status === "new" && <Badge tone="warn">New</Badge>}
        </span>
        <span className="sub" style={{ display: "block" }}>
          {cameraName} · {event.type === "PERSON_DETECTED" ? `track ${event.track_id}` : describeEvent(event)} · {percent(event.confidence)} ·{" "}
          {relativeTime(event.timestamp, now)}
        </span>
      </span>
    </button>
  );
}
