import { useCameraHealth, useCameras, useEvents } from "../hooks";
import { formatTime, percent, seconds } from "../format";
import { Badge } from "./Badge";
import { VideoPlayer } from "./VideoPlayer";

function Status({ cameraId }: { cameraId: number }) {
  const { status, stream, health, lastError } = useCameraHealth(cameraId);
  const engine = status?.engine;
  return (
    <aside className="panel">
      <h3>
        Pipeline <Badge tone={health.tone}>{health.label}</Badge>
      </h3>
      <dl>
        <dt>FPS</dt>
        <dd>{stream?.reporting && stream.fps !== null ? stream.fps.toFixed(1) : "-"}</dd>
        <dt>Last frame</dt>
        <dd>{stream?.reporting ? `${seconds(stream.last_frame_age)} ago` : "-"}</dd>
        <dt>Heartbeat</dt>
        <dd>{stream?.heartbeat_age != null ? `${seconds(stream.heartbeat_age)} ago` : "never"}</dd>
        <dt>Engine</dt>
        <dd>{engine ? (engine.manager === "reachable" ? engine.state : engine.manager) : "-"}</dd>
        <dt>Starts</dt>
        <dd>{engine?.starts ?? "-"}</dd>
        <dt>Config applied</dt>
        <dd>{engine?.applied == null ? "-" : engine.applied ? "yes" : "pending"}</dd>
      </dl>
      <h4>Last error</h4>
      <p className={lastError ? "error" : "muted"}>{lastError ?? "None"}</p>
    </aside>
  );
}

function RecentEvents({ cameraId }: { cameraId: number }) {
  const events = useEvents({ cameraId, limit: 5, offset: 0 });
  return (
    <aside className="panel">
      <h3>Latest events</h3>
      {events.data?.items.length === 0 && <p className="muted">None yet.</p>}
      <ul className="plain">
        {events.data?.items.map((e) => (
          <li key={e.event_id}>
            {formatTime(e.timestamp)} · track {e.track_id} · {percent(e.confidence)}
          </li>
        ))}
      </ul>
    </aside>
  );
}

export function Monitoring({ cameraId, onSelect }: { cameraId: number | null; onSelect: (id: number) => void }) {
  const cameras = useCameras();
  if (cameras.isPending) return <p className="muted">Loading...</p>;
  if (cameras.isError) return <p className="error">Cannot load cameras: {cameras.error.message}</p>;
  if (cameras.data.length === 0) return <p className="muted">No cameras yet.</p>;

  const selected = cameras.data.find((c) => c.id === cameraId) ?? cameras.data[0];
  const shouldRun = selected.enabled && selected.desired_state === "running";

  return (
    <section>
      <label className="field">
        Camera
        <select value={selected.id} onChange={(e) => onSelect(Number(e.target.value))}>
          {cameras.data.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </label>
      <div className="monitor">
        <VideoPlayer key={selected.id} cameraId={selected.id} shouldRun={shouldRun} />
        <div className="side">
          <Status cameraId={selected.id} />
          <RecentEvents cameraId={selected.id} />
        </div>
      </div>
    </section>
  );
}
