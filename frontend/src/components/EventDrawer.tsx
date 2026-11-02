import { Check, ImageOff, RotateCcw, Video } from "lucide-react";
import { useState } from "react";
import { useCameraName, useEvent, useSetEventStatus } from "../hooks/queries";
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
          <dd>Person detected</dd>
          <dt>Camera</dt>
          <dd>{cameraName(e.camera_id)}</dd>
          <dt>Time</dt>
          <dd>
            {formatTime(e.timestamp)}
            <div className="muted">{relativeTime(e.timestamp)}</div>
          </dd>
          <dt>Confidence</dt>
          <dd>{percent(e.confidence)}</dd>
          <dt>Track ID</dt>
          <dd>{e.track_id}</dd>
          <dt>Status</dt>
          <dd>
            <Badge tone={acknowledged ? "idle" : "warn"}>{acknowledged ? "Acknowledged" : "New"}</Badge>
          </dd>
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
