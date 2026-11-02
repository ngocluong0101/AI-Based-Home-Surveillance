import { useCameraControl, useCameraHealth, useCameras, useEventsToday } from "../hooks";
import type { Camera } from "../api";
import { seconds } from "../format";
import { Badge } from "./Badge";

function CameraCard({ camera, onOpen }: { camera: Camera; onOpen: (id: number) => void }) {
  const { status, stream, health, lastError } = useCameraHealth(camera.id);
  const today = useEventsToday(camera.id);
  const control = useCameraControl();
  const shouldRun = status?.should_run ?? camera.desired_state === "running";

  return (
    <article className="card">
      <header>
        <h3>{camera.name}</h3>
        <Badge tone={health.tone}>{health.label}</Badge>
      </header>
      <dl>
        <dt>FPS</dt>
        <dd>{stream?.reporting && stream.fps !== null ? stream.fps.toFixed(1) : "-"}</dd>
        <dt>Last frame</dt>
        <dd>{stream?.reporting ? `${seconds(stream.last_frame_age)} ago` : "-"}</dd>
        <dt>People today</dt>
        <dd>{today.data ?? "-"}</dd>
        <dt>Source</dt>
        <dd className="mono" title={camera.source_uri}>
          {camera.source_type}
        </dd>
      </dl>
      {lastError && <p className="error">{lastError}</p>}
      {control.isError && <p className="error">{(control.error as Error).message}</p>}
      <footer>
        <button onClick={() => onOpen(camera.id)}>Watch</button>
        <button
          disabled={control.isPending}
          onClick={() => control.mutate({ id: camera.id, run: !shouldRun })}
        >
          {shouldRun ? "Stop" : "Start"}
        </button>
      </footer>
    </article>
  );
}

export function Dashboard({ onOpenCamera }: { onOpenCamera: (id: number) => void }) {
  const cameras = useCameras();
  const today = useEventsToday();

  if (cameras.isPending) return <p className="muted">Loading...</p>;
  if (cameras.isError) return <p className="error">Cannot load cameras: {cameras.error.message}</p>;

  return (
    <section>
      <div className="summary">
        <div>
          <span className="big">{cameras.data.length}</span>
          <span className="muted">cameras</span>
        </div>
        <div>
          <span className="big">{today.data ?? "-"}</span>
          <span className="muted">events today</span>
        </div>
      </div>
      {cameras.data.length === 0 ? (
        <p className="muted">
          No cameras yet. Create one with <code>POST /api/cameras</code>; the camera form comes with the next phase.
        </p>
      ) : (
        <div className="grid">
          {cameras.data.map((c) => (
            <CameraCard key={c.id} camera={c} onOpen={onOpenCamera} />
          ))}
        </div>
      )}
    </section>
  );
}
