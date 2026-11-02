import { useState } from "react";
import type { Camera, PersonEvent } from "../api";
import { formatTime, localDayStart, percent } from "../format";
import { useAcknowledge, useCameras, useEvents } from "../hooks";
import { Badge } from "./Badge";

const PAGE = 25;

function SnapshotModal({ event, onClose }: { event: PersonEvent; onClose: () => void }) {
  const [x, y, w, h] = event.bbox;
  return (
    <div className="modal" onClick={onClose} role="dialog" aria-modal="true">
      <div className="modal-body" onClick={(e) => e.stopPropagation()}>
        <div className="snapshot">
          <img src={event.snapshot_url!} alt={`Snapshot of event ${event.event_id}`} />
          <div
            className="bbox"
            style={{ left: `${x * 100}%`, top: `${y * 100}%`, width: `${w * 100}%`, height: `${h * 100}%` }}
          />
        </div>
        <button onClick={onClose}>Close</button>
      </div>
    </div>
  );
}

export function Events() {
  const cameras = useCameras();
  const [cameraId, setCameraId] = useState<number | undefined>();
  const [status, setStatus] = useState<"new" | "acknowledged" | undefined>();
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<PersonEvent | null>(null);
  const ack = useAcknowledge();

  const events = useEvents({
    cameraId,
    status,
    from: localDayStart(from),
    to: localDayStart(to, 1), // the whole last day: exclusive bound is the next midnight
    limit: PAGE,
    offset,
  });

  const names = new Map<number, Camera>((cameras.data ?? []).map((c) => [c.id, c]));
  const filter = <T,>(set: (v: T) => void) => (v: T) => {
    set(v);
    setOffset(0);
  };
  const total = events.data?.total ?? 0;

  return (
    <section>
      <div className="filters">
        <label className="field">
          Camera
          <select
            value={cameraId ?? ""}
            onChange={(e) => filter(setCameraId)(e.target.value ? Number(e.target.value) : undefined)}
          >
            <option value="">All</option>
            {cameras.data?.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Status
          <select
            value={status ?? ""}
            onChange={(e) => filter(setStatus)((e.target.value || undefined) as typeof status)}
          >
            <option value="">All</option>
            <option value="new">New</option>
            <option value="acknowledged">Acknowledged</option>
          </select>
        </label>
        <label className="field">
          From
          <input type="date" value={from} onChange={(e) => filter(setFrom)(e.target.value)} />
        </label>
        <label className="field">
          To
          <input type="date" value={to} onChange={(e) => filter(setTo)(e.target.value)} />
        </label>
      </div>

      {events.isError && <p className="error">Cannot load events: {events.error.message}</p>}
      {ack.isError && <p className="error">{(ack.error as Error).message}</p>}

      <table>
        <thead>
          <tr>
            <th>Snapshot</th>
            <th>Time</th>
            <th>Camera</th>
            <th>Track</th>
            <th>Confidence</th>
            <th>Telegram</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {events.data?.items.map((e) => (
            <tr key={e.event_id}>
              <td>
                {e.snapshot_url ? (
                  <button className="thumb" onClick={() => setOpen(e)} aria-label="Open snapshot">
                    <img src={e.snapshot_url} alt="" loading="lazy" />
                  </button>
                ) : (
                  <span className="muted">none</span>
                )}
              </td>
              <td>{formatTime(e.timestamp)}</td>
              <td>{names.get(e.camera_id)?.name ?? `#${e.camera_id}`}</td>
              <td>{e.track_id}</td>
              <td>{percent(e.confidence)}</td>
              <td>{e.notify_status}</td>
              <td>
                <Badge tone={e.status === "new" ? "warn" : "idle"}>{e.status}</Badge>{" "}
                <button
                  disabled={ack.isPending}
                  onClick={() =>
                    ack.mutate({ id: e.event_id, status: e.status === "new" ? "acknowledged" : "new" })
                  }
                >
                  {e.status === "new" ? "Acknowledge" : "Reopen"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {events.data?.items.length === 0 && <p className="muted">No events match.</p>}

      <div className="pager">
        <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
          Previous
        </button>
        <span className="muted">
          {total === 0 ? 0 : offset + 1}-{Math.min(offset + PAGE, total)} of {total}
        </span>
        <button disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>
          Next
        </button>
      </div>

      {open && <SnapshotModal event={open} onClose={() => setOpen(null)} />}
    </section>
  );
}
