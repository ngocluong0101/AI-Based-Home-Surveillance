import { AnimatePresence, motion } from "motion/react";
import { Activity, Camera as CameraIcon, Clock, Gauge, UserRound } from "lucide-react";
import { CircleAlert, CircleCheck } from "lucide-react";
import { EventItem } from "../components/EventItem";
import { VideoPlayer } from "../components/VideoPlayer";
import { Badge } from "../components/ui/Badge";
import { Skeleton } from "../components/ui/Skeleton";
import { StatCard } from "../components/ui/StatCard";
import { StatusDot } from "../components/ui/StatusDot";
import { EmptyState, ErrorState } from "../components/ui/States";
import { useCameraName, useCameras, useEvents, useEventsToday, useFleet, type FleetItem } from "../hooks/queries";
import { useOpenEvent } from "../hooks/useEventDrawer";
import { useNow } from "../hooks/useNow";
import { clockTime, seconds } from "../utils/format";
import { useHighlighted } from "../websocket/recentEvents";

function CameraList({ fleet, selectedId, onSelect }: { fleet: FleetItem[]; selectedId: number; onSelect: (id: number) => void }) {
  return (
    <nav className="card cam-list" aria-label="Cameras">
      {fleet.map(({ camera, health, stream }) => (
        <button key={camera.id} className={`cam-item ${camera.id === selectedId ? "selected" : ""}`} onClick={() => onSelect(camera.id)} aria-current={camera.id === selectedId}>
          <StatusDot tone={health.tone} pulse={health.tone === "ok"} />
          <span className="grow">
            <span className="name" style={{ display: "block" }}>
              {camera.name}
            </span>
            <span className="sub">
              {health.label}
              {stream?.reporting && stream.fps !== null ? ` · ${stream.fps.toFixed(0)} fps` : ""}
            </span>
          </span>
        </button>
      ))}
    </nav>
  );
}

function LatestEvents({ cameraId }: { cameraId: number }) {
  const events = useEvents({ cameraId, limit: 5, offset: 0 });
  const cameraName = useCameraName();
  const open = useOpenEvent();
  const highlighted = useHighlighted();
  const now = useNow();
  return (
    <section className="card panel">
      <div className="panel-title">
        Latest events <a href={`#/events`}>View all</a>
      </div>
      {events.isPending ? (
        <Skeleton height={60} />
      ) : !events.data ? (
        <ErrorState title="Failed to load events" message={events.error.message} onRetry={() => void events.refetch()} compact />
      ) : events.data.items.length === 0 ? (
        <EmptyState icon={UserRound} title="No events yet" hint="Nobody has been detected on this camera." compact />
      ) : (
        <ul className="event-list">
          {events.data.items.map((e) => (
            <li key={e.event_id}>
              <EventItem event={e} cameraName={cameraName(e.camera_id)} now={now} highlighted={highlighted.has(e.event_id)} onOpen={open} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Stage({ item }: { item: FleetItem }) {
  const { camera, stream, status, health, lastError, updatedAt } = item;
  const today = useEventsToday(camera.id);
  const engine = status?.engine;
  const shouldRun = status?.should_run ?? camera.desired_state === "running";
  const fps = stream?.reporting && stream.fps !== null ? stream.fps.toFixed(1) : "-";

  return (
    <div className="stage">
      <div className="stage-head">
        <h2>
          <StatusDot tone={health.tone} pulse={health.tone === "ok"} />
          {camera.name}
          <Badge tone={health.tone}>{health.label}</Badge>
        </h2>
        <span className="muted" style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <Clock size={14} /> Updated {updatedAt ? clockTime(updatedAt) : "never"}
        </span>
      </div>

      <VideoPlayer key={camera.id} cameraId={camera.id} shouldRun={shouldRun} health={health} />

      <div className="strip">
        <StatCard icon={Gauge} label="FPS" value={fps} tone={health.tone === "ok" ? "ok" : "idle"} />
        <StatCard icon={UserRound} label="People today" value={today.data} hint="events today" loading={today.isPending} />
        <StatCard icon={Activity} label="Status" value={health.label} tone={health.tone === "ok" ? "ok" : health.tone === "idle" ? "idle" : "warn"} />
        <StatCard icon={Clock} label="Last frame" value={stream?.reporting ? `${seconds(stream.last_frame_age)}` : "-"} hint="ago" />
      </div>

      <div className="detail-grid">
        <section className="card panel">
          <div className="panel-title">Pipeline</div>
          <dl>
            <dt>Engine</dt>
            <dd>{engine ? (engine.manager === "reachable" ? engine.state : engine.manager.replace("_", " ")) : "-"}</dd>
            <dt>Heartbeat</dt>
            <dd>{stream?.heartbeat_age != null ? `${seconds(stream.heartbeat_age)} ago` : "never"}</dd>
            <dt>Starts</dt>
            <dd>{engine?.starts ?? "-"}</dd>
            <dt>Config applied</dt>
            <dd>{engine?.applied == null ? "-" : engine.applied ? "yes" : "pending"}</dd>
          </dl>
          {lastError ? (
            <div className="errbox" role="alert">
              <CircleAlert size={18} style={{ flex: "none" }} />
              <div>
                <strong>Last error</strong>
                <div>{lastError}</div>
              </div>
            </div>
          ) : (
            <div className="okbox">
              <CircleCheck size={18} style={{ flex: "none" }} /> No errors
            </div>
          )}
        </section>
        <LatestEvents cameraId={camera.id} />
      </div>
    </div>
  );
}

export function MonitoringPage({ cameraId, onSelect }: { cameraId: number | null; onSelect: (id: number) => void }) {
  const cameras = useCameras();
  const fleet = useFleet(cameras.data);

  let content;
  if (cameras.isPending) {
    content = (
      <div className="monitor">
        <div className="card cam-list">
          <Skeleton height={44} />
          <Skeleton height={44} />
        </div>
        <Skeleton height={420} style={{ borderRadius: 12 }} />
      </div>
    );
  } else if (!cameras.data) {
    content = <ErrorState title="Failed to load cameras" message={cameras.error.message} onRetry={() => void cameras.refetch()} />;
  } else if (cameras.data.length === 0) {
    content = <EmptyState icon={CameraIcon} title="No cameras found" hint={<>Add one with <code>POST /api/cameras</code>.</>} />;
  } else {
    const selected = fleet.find((f) => f.camera.id === cameraId) ?? fleet[0];
    if (selected) {
      content = (
        <div className="monitor">
          <CameraList fleet={fleet} selectedId={selected.camera.id} onSelect={onSelect} />
          <AnimatePresence mode="wait" initial={false}>
            <motion.div key={selected.camera.id} initial={{ opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.16 }}>
              <Stage item={selected} />
            </motion.div>
          </AnimatePresence>
        </div>
      );
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Monitoring</h1>
          <p>Live video with detections, and the health of each camera pipeline.</p>
        </div>
      </div>
      {content}
    </div>
  );
}
