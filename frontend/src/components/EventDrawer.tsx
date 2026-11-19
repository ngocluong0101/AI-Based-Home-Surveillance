import { verificationNote } from "../utils/eventType";
import { Check, ImageOff, RotateCcw, Video } from "lucide-react";
import { useState } from "react";
import { useCameraName, useEvent, useSetEventStatus } from "../hooks/queries";
import { describeEvent, kindOf, SEVERITY_LABEL, SEVERITY_TONE } from "../utils/eventType";
import { formatTime, percent, relativeTime } from "../utils/format";
import { Badge } from "./ui/Badge";
import { Drawer } from "./ui/Drawer";
import { Spinner } from "./ui/Spinner";
import { EmptyState, ErrorState } from "./ui/States";

function Snapshot({ src, bbox }: { src: string; bbox: [number, number, number, number] }) {
  const [failed, setFailed] = useState(false);
  const [x, y, w, h] = bbox;
  if (failed) return <EmptyState icon={ImageOff} title="Snapshot no longer available" hint="Old snapshots are deleted automatically." compact />;
  return (
    <div className="snapshot">
      <img src={src} alt="Snapshot of the detection" onError={() => setFailed(true)} />
      <div className="bbox" style={{ left: `${x * 100}%`, top: `${y * 100}%`, width: `${w * 100}%`, height: `${h * 100}%` }} />
    </div>
  );
}

function Body({ eventId, onOpenCamera }: { eventId: string; onOpenCamera: (cameraId: number) => void }) {
  const event = useEvent(eventId);
  const cameraName = useCameraName();
  const setStatus = useSetEventStatus();

  if (event.isPending) {
    return (
      <div className="drawer-body" style={{ alignItems: "center", justifyContent: "center" }}>
        <Spinner />
      </div>
    );
  }
  if (event.isError) {
    return (
      <div className="drawer-body">
        <ErrorState title="Failed to load the event" message={event.error.message} onRetry={() => void event.refetch()} />
      </div>
    );
  }
  const e = event.data;
  const acknowledged = e.status === "acknowledged";
  return (
    <>
      <div className="drawer-body">
        {e.snapshot_url ? (
          <Snapshot src={e.snapshot_url} bbox={e.bbox} />
        ) : (
          <EmptyState icon={ImageOff} title="No snapshot" hint="This event was saved without an image." compact />
        )}
        <dl>
          <dt>Event</dt>
          <dd>
            {kindOf(e.type).label}{" "}
            {e.severity !== "info" && <Badge tone={SEVERITY_TONE[e.severity]}>{SEVERITY_LABEL[e.severity]}</Badge>}
            {e.type !== "PERSON_DETECTED" && <div className="muted">{describeEvent(e)}</div>}
          </dd>
          <dt>Camera</dt>
          <dd>{cameraName(e.camera_id)}</dd>
          <dt>{e.occurred_at ? "Reported" : "Time"}</dt>
          <dd>
            {formatTime(e.timestamp)}
            <div className="muted">{relativeTime(e.timestamp)}</div>
          </dd>
          {e.occurred_at && (
            <>
              <dt>Began</dt>
              <dd>
                {formatTime(e.occurred_at)}
                <div className="muted">{e.type === "FALL" ? "the moment the person hit the floor" : "when the behaviour started"}</div>
              </dd>
            </>
          )}
          <dt>Confidence</dt>
          <dd>{percent(e.confidence)}</dd>
          <dt>{e.track_ids.length > 1 ? "Track IDs" : "Track ID"}</dt>
          <dd>{e.track_ids.length > 0 ? e.track_ids.join(", ") : e.track_id}</dd>
          {e.zone_id !== null && (
            <>
              <dt>Zone</dt>
              <dd>{e.zone_id}</dd>
            </>
          )}
          <dt>Status</dt>
          <dd>
            <Badge tone={acknowledged ? "idle" : "warn"}>{acknowledged ? "Acknowledged" : "New"}</Badge>
          </dd>
          {verificationNote(e) && (
            <>
              <dt>VLM check</dt>
              <dd>
                <Badge tone={verificationNote(e)!.tone}>{verificationNote(e)!.label}</Badge>
                {verificationNote(e)!.detail && <div className="muted" style={{ marginTop: 4 }}>{verificationNote(e)!.detail}</div>}
              </dd>
            </>
          )}
          <dt>Telegram</dt>
          <dd>{e.notify_status}</dd>
          <dt>Event ID</dt>
          <dd className="mono muted" style={{ fontSize: "0.75rem" }}>
            {e.event_id}
          </dd>
        </dl>
        {setStatus.isError && <ErrorState title="Could not update the event" message={setStatus.error.message} compact />}
      </div>
      <div className="drawer-foot">
        <button className="btn" onClick={() => onOpenCamera(e.camera_id)}>
          <Video size={16} /> Open camera
        </button>
        <button
          className={`btn ${acknowledged ? "" : "btn-primary"}`}
          disabled={setStatus.isPending}
          onClick={() => setStatus.mutate({ id: e.event_id, status: acknowledged ? "new" : "acknowledged" })}
        >
          {acknowledged ? <RotateCcw size={16} /> : <Check size={16} />}
          {acknowledged ? "Mark as new" : "Acknowledge"}
        </button>
      </div>
    </>
  );
}

/** Slide-in panel with everything about one event. */
export function EventDrawer({
  eventId,
  onClose,
  onOpenCamera,
}: {
  eventId: string | null;
  onClose: () => void;
  onOpenCamera: (cameraId: number) => void;
}) {
  return (
    <Drawer open={eventId !== null} title="Event details" onClose={onClose}>
      {eventId && <Body eventId={eventId} onOpenCamera={onOpenCamera} />}
    </Drawer>
  );
}
