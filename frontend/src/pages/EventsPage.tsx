import { Bell, ChevronLeft, ChevronRight, FilterX, LayoutGrid, List } from "lucide-react";
import { useState } from "react";
import { ConfidenceBar } from "../components/ConfidenceBar";
import { EventCard } from "../components/EventCard";
import { Thumb } from "../components/Thumb";
import { Badge } from "../components/ui/Badge";
import { Segmented } from "../components/ui/Segmented";
import { Skeleton } from "../components/ui/Skeleton";
import { EmptyState, ErrorState } from "../components/ui/States";
import { useCameraName, useCameras, useEvents } from "../hooks/queries";
import { useOpenEvent } from "../hooks/useEventDrawer";
import { useNow } from "../hooks/useNow";
import type { EventStatus } from "../types";
import { formatTime, groupByDay, localDayStart, relativeTime } from "../utils/format";
import { useHighlighted } from "../websocket/recentEvents";

const PAGE = 24;
const COLUMNS = 7;

type View = "gallery" | "table";
const VIEW_KEY = "events-view";
const savedView = (): View => (localStorage.getItem(VIEW_KEY) === "table" ? "table" : "gallery");

export function EventsPage() {
  const cameras = useCameras();
  const cameraName = useCameraName();
  const open = useOpenEvent();
  const highlighted = useHighlighted();
  const now = useNow();

  const [cameraId, setCameraId] = useState<number | undefined>();
  const [status, setStatus] = useState<EventStatus | undefined>();
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [offset, setOffset] = useState(0);
  const [view, setViewState] = useState<View>(savedView);
  const setView = (v: View) => {
    localStorage.setItem(VIEW_KEY, v);
    setViewState(v);
  };

  const events = useEvents({
    cameraId,
    status,
    from: localDayStart(from),
    to: localDayStart(to, 1), // the whole last day: the exclusive bound is the next midnight
    limit: PAGE,
    offset,
  });

  // Any filter change goes back to the first page.
  const filter =
    <T,>(set: (v: T) => void) =>
    (v: T) => {
      set(v);
      setOffset(0);
    };
  const filtered = cameraId !== undefined || status !== undefined || from !== "" || to !== "";
  const clear = () => {
    setCameraId(undefined);
    setStatus(undefined);
    setFrom("");
    setTo("");
    setOffset(0);
  };
  const total = events.data?.total ?? 0;
  const dim = { opacity: events.isPlaceholderData ? 0.55 : 1, transition: "opacity 0.15s" };

  const gallery = events.data ? (
    <div className="gallery" style={dim}>
      {groupByDay(events.data.items, now).map((g) => (
        <section key={g.key} aria-labelledby={`day-${g.key}`}>
          <h2 className="day" id={`day-${g.key}`}>
            {g.label} <span>{g.items.length}</span>
          </h2>
          <div className="shots">
            {g.items.map((e) => (
              <EventCard key={e.event_id} event={e} cameraName={cameraName(e.camera_id)} now={now} highlighted={highlighted.has(e.event_id)} onOpen={open} />
            ))}
          </div>
        </section>
      ))}
    </div>
  ) : (
    <div className="gallery">
      <div className="shots">
        {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => (
          <div key={i} className="shot">
            <Skeleton style={{ aspectRatio: "16 / 9", height: "auto", borderRadius: 8 }} />
            <Skeleton width="60%" style={{ marginTop: 10 }} />
          </div>
        ))}
      </div>
    </div>
  );

  let rows;
  if (events.isPending) {
    rows = [0, 1, 2, 3, 4, 5].map((i) => (
      <tr key={i}>
        <td><Skeleton width={88} height={50} /></td>
        {Array.from({ length: COLUMNS - 1 }, (_, c) => (
          <td key={c}><Skeleton width={c === 0 ? 130 : 70} /></td>
        ))}
      </tr>
    ));
  } else if (events.data && events.data.items.length > 0) {
    rows = events.data.items.map((e) => (
      <tr key={e.event_id} className={`clickable ${highlighted.has(e.event_id) ? "is-new" : ""}`} onClick={() => open(e.event_id)}>
        <td><Thumb src={e.snapshot_url} /></td>
        <td>
          {formatTime(e.timestamp)}
          <div className="muted" style={{ fontSize: "0.78rem" }}>{relativeTime(e.timestamp, now)}</div>
        </td>
        <td>{cameraName(e.camera_id)}</td>
        <td>Person detected</td>
        <td>{e.track_id}</td>
        <td><ConfidenceBar value={e.confidence} /></td>
        <td><Badge tone={e.status === "new" ? "warn" : "idle"}>{e.status === "new" ? "New" : "Acknowledged"}</Badge></td>
      </tr>
    ));
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Events</h1>
          <p>Every person detected, newest first. New events appear as they happen.</p>
        </div>
      </div>

      <div className="card toolbar">
        <label className="field">
          Camera
          <select value={cameraId ?? ""} onChange={(e) => filter(setCameraId)(e.target.value ? Number(e.target.value) : undefined)}>
            <option value="">All cameras</option>
            {cameras.data?.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
        </label>
        <label className="field">
          Status
          <select value={status ?? ""} onChange={(e) => filter(setStatus)((e.target.value || undefined) as EventStatus | undefined)}>
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
        <span className="spacer" />
        {filtered && (
          <button className="btn btn-ghost" onClick={clear}>
            <FilterX size={16} /> Clear filters
          </button>
        )}
        <Segmented
          label="View"
          value={view}
          onChange={setView}
          options={[
            { value: "gallery", label: "Gallery", icon: <LayoutGrid size={15} aria-hidden="true" /> },
            { value: "table", label: "Table", icon: <List size={15} aria-hidden="true" /> },
          ]}
        />
      </div>

      <div className="card">
        {events.isError && !events.data ? (
          <ErrorState title="Failed to load events" message={events.error.message} onRetry={() => void events.refetch()} />
        ) : events.data && events.data.items.length === 0 ? (
          <EmptyState
            icon={Bell}
            title={filtered ? "No events match these filters" : "No events yet"}
            hint={filtered ? "Try a wider date range or clear the filters." : "When a person is detected, the event shows up here."}
          >
            {filtered && <button className="btn btn-sm" onClick={clear}>Clear filters</button>}
          </EmptyState>
        ) : view === "gallery" ? (
          gallery
        ) : (
          <div className="table-wrap" style={dim}>
            <table>
              <thead>
                <tr>
                  <th>Snapshot</th>
                  <th>Timestamp</th>
                  <th>Camera</th>
                  <th>Event</th>
                  <th>Track</th>
                  <th>Confidence</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>{rows}</tbody>
            </table>
          </div>
        )}
        {total > 0 && (
          <div className="pager">
            <span className="muted">
              {offset + 1}-{Math.min(offset + PAGE, total)} of {total}
            </span>
            <div className="btns">
              <button className="btn btn-sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
                <ChevronLeft size={15} /> Previous
              </button>
              <button className="btn btn-sm" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>
                Next <ChevronRight size={15} />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
