import { AnimatePresence, motion } from "motion/react";
import { Activity, Bell, Camera as CameraIcon, CircleAlert, Play, Square, Users, Video, Wifi } from "lucide-react";
import { CoverImage } from "../components/CoverImage";
import { EventItem } from "../components/EventItem";
import { LiveChips } from "../components/LiveChips";
import { Badge } from "../components/ui/Badge";
import { Skeleton } from "../components/ui/Skeleton";
import { StatCard } from "../components/ui/StatCard";
import { StatusDot } from "../components/ui/StatusDot";
import { EmptyState, ErrorState } from "../components/ui/States";
import {
  useCameraControl,
  useCameraName,
  useCameras,
  useEvents,
  useEventsToday,
  useEventsTodayByCamera,
  useFleet,
  useLatestSnapshot,
  type FleetItem,
} from "../hooks/queries";
import { useOpenEvent } from "../hooks/useEventDrawer";
import { useNow } from "../hooks/useNow";
import { relativeTime } from "../utils/format";
import { totalPeople } from "../utils/health";
import { useHighlighted } from "../websocket/recentEvents";

/** A camera: the picture of its last detection as the cover (click = watch live), what it is doing, and start/stop. */
function CameraCard({ item, today, onWatch }: { item: FleetItem; today: number | undefined; onWatch: (id: number) => void }) {
  const { camera, stream, health, lastError, status } = item;
  const control = useCameraControl();
  const latest = useLatestSnapshot(camera.id);
  const now = useNow();
  const shouldRun = status?.should_run ?? camera.desired_state === "running";
  const online = health.tone === "ok";
  return (
    <article className={`card cam-card ${online ? "is-online" : ""}`}>
      <button className="cover" onClick={() => onWatch(camera.id)} aria-label={`Watch ${camera.name}`}>
        <CoverImage src={latest.url} dim={!online} />
        <LiveChips stream={stream} />
        <span className="cover-note">
          {latest.at ? `Last detection ${relativeTime(latest.at, now)}` : latest.pending ? "Loading..." : "No detections yet"}
        </span>
        <span className="cover-play" aria-hidden="true">
          <Video size={18} />
        </span>
      </button>
      <div className="cam-head">
        <h3>
          <StatusDot tone={health.tone} pulse={online} />
          <span>{camera.name}</span>
        </h3>
        <Badge tone={health.tone}>{health.label}</Badge>
      </div>
      {lastError && (
        <p className="err-text">
          <CircleAlert size={15} style={{ flex: "none", marginTop: 2 }} /> {lastError}
        </p>
      )}
      {control.isError && <p className="err-text">{control.error.message}</p>}
      <div className="cam-foot">
        <span className="muted">
          <b>{today ?? "-"}</b> events today
        </span>
        <button className="btn btn-sm" disabled={control.isPending} onClick={() => control.mutate({ id: camera.id, run: !shouldRun })}>
          {shouldRun ? <Square size={14} /> : <Play size={14} />}
          {shouldRun ? "Stop" : "Start"}
        </button>
      </div>
    </article>
  );
}

function CameraSkeletons() {
  return (
    <div className="cam-grid">
      {[0, 1].map((i) => (
        <div key={i} className="card cam-card">
          <Skeleton height={140} />
          <Skeleton width="60%" height={18} />
          <Skeleton height={30} />
        </div>
      ))}
    </div>
  );
}

function RecentEvents() {
  const events = useEvents({ limit: 8, offset: 0 });
  const cameraName = useCameraName();
  const open = useOpenEvent();
  const highlighted = useHighlighted();
  const now = useNow();

  let body;
  if (events.isPending) {
    body = (
      <ul className="event-list">
        {[0, 1, 2, 3].map((i) => (
          <li key={i} style={{ display: "flex", gap: 12, padding: 8 }}>
            <Skeleton width={88} height={50} />
            <span style={{ flex: 1, display: "flex", flexDirection: "column", gap: 8 }}>
              <Skeleton width="40%" />
              <Skeleton width="70%" />
            </span>
          </li>
        ))}
      </ul>
    );
  } else if (events.isError && !events.data) {
    body = <ErrorState title="Failed to load events" message={events.error.message} onRetry={() => void events.refetch()} compact />;
  } else if (events.data.items.length === 0) {
    body = <EmptyState icon={Bell} title="No recent activity" hint="Events appear here the moment a person is detected." compact />;
  } else {
    body = (
      <ul className="event-list">
        <AnimatePresence initial={false}>
          {events.data.items.map((e) => (
            <motion.li
              key={e.event_id}
              layout
              initial={{ opacity: 0, y: -12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
            >
              <EventItem event={e} cameraName={cameraName(e.camera_id)} now={now} highlighted={highlighted.has(e.event_id)} onOpen={open} />
            </motion.li>
          ))}
        </AnimatePresence>
      </ul>
    );
  }

  return (
    <section className="card panel">
      <div className="panel-title">
        Recent events <a href="#/events">View all</a>
      </div>
      {body}
    </section>
  );
}

export function DashboardPage({ onOpenCamera }: { onOpenCamera: (id: number) => void }) {
  const cameras = useCameras();
  const fleet = useFleet(cameras.data);
  const perCamera = useEventsTodayByCamera(cameras.data ?? []);
  const today = useEventsToday();

  const total = cameras.data?.length;
  const online = fleet.filter((f) => f.health.tone === "ok").length;
  const maxToday = Math.max(1, ...perCamera.map((n) => n ?? 0));
  const loading = cameras.isPending;
  const people = totalPeople(fleet.map((f) => f.stream));

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Dashboard</h1>
          <p>Live overview of your cameras and detections.</p>
        </div>
      </div>

      <div className="stats">
        <StatCard icon={CameraIcon} label="Cameras" value={total} hint="configured" loading={loading} />
        <StatCard
          icon={Wifi}
          label="Online"
          value={total === undefined ? undefined : `${online} / ${total}`}
          hint={total === undefined ? undefined : `${total - online} offline`}
          tone={online > 0 ? "ok" : "idle"}
          loading={loading}
        />
        <StatCard
          icon={Users}
          label="People now"
          value={people ?? "-"}
          hint={people === null ? "no camera is reporting" : "in view right now"}
          tone={people ? "warn" : "idle"}
          loading={loading}
        />
        <StatCard icon={Activity} label="Events today" value={today.data} hint="people detected" loading={today.isPending} />
      </div>

      <div className="dash-grid">
        <RecentEvents />
        <section className="card panel">
          <div className="panel-title">
            Cameras <a href="#/monitoring">Open monitoring</a>
          </div>
          {cameras.isPending ? (
            <CameraSkeletons />
          ) : !cameras.data ? (
            <ErrorState title="Failed to load cameras" message={cameras.error.message} onRetry={() => void cameras.refetch()} />
          ) : cameras.data.length === 0 ? (
            <EmptyState icon={CameraIcon} title="No cameras found" hint="Add an IP camera or a video file to start detecting people.">
              <a className="btn btn-primary" href="#/cameras">Add a camera</a>
            </EmptyState>
          ) : (
            <>
              <div className="cam-grid">
                {fleet.map((item, i) => (
                  <motion.div key={item.camera.id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.04, duration: 0.2 }}>
                    <CameraCard item={item} today={perCamera[i]} onWatch={onOpenCamera} />
                  </motion.div>
                ))}
              </div>
              {cameras.data.length > 1 && (
                <div style={{ marginTop: "1.25rem" }}>
                  <div className="panel-title">Events today by camera</div>
                  <div className="bars">
                    {cameras.data.map((c, i) => (
                      <div className="bar-row" key={c.id}>
                        <span className="name">{c.name}</span>
                        <div className="bar-track">
                          <motion.div
                            className="bar-fill"
                            initial={{ width: 0 }}
                            animate={{ width: `${((perCamera[i] ?? 0) / maxToday) * 100}%` }}
                            transition={{ duration: 0.4 }}
                          />
                        </div>
                        <span className="n">{perCamera[i] ?? "-"}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
