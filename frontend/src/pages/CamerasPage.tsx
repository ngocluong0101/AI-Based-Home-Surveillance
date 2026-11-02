import { Cctv, Pencil, Play, Plus, PlugZap, Square, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { CameraFormDrawer } from "../components/CameraFormDrawer";
import { Badge } from "../components/ui/Badge";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { Skeleton } from "../components/ui/Skeleton";
import { StatusDot } from "../components/ui/StatusDot";
import { EmptyState, ErrorState } from "../components/ui/States";
import { useCameraControl, useCameras, useDeleteCamera, useFleet, useTestConnection, type FleetItem } from "../hooks/queries";
import type { Camera } from "../types";

function Row({ item, onEdit, onDelete }: { item: FleetItem; onEdit: (c: Camera) => void; onDelete: (c: Camera) => void }) {
  const { camera, health, status } = item;
  const control = useCameraControl();
  const test = useTestConnection();
  const shouldRun = status?.should_run ?? camera.desired_state === "running";

  const runTest = () =>
    test.mutate(
      { camera_id: camera.id },
      {
        onSuccess: (r) => (r.ok ? toast.success(camera.name, { description: r.summary }) : toast.error(`${camera.name}: test failed`, { description: r.summary })),
        onError: (e) => toast.error(`${camera.name}: the test could not run`, { description: e.message }),
      },
    );

  return (
    <tr>
      <td>
        <div className="cell-name">
          <StatusDot tone={health.tone} pulse={health.tone === "ok"} />
          <b>{camera.name}</b>
        </div>
      </td>
      <td>
        <Badge tone="accent">{camera.source_type === "rtsp" ? "RTSP" : "File"}</Badge>{" "}
        <span className="mono muted src" title={camera.source_uri}>
          {camera.source_uri}
        </span>
      </td>
      <td>
        <Badge tone={health.tone}>{health.label}</Badge>
        {!camera.enabled && (
          <>
            {" "}
            <Badge tone="idle">Disabled</Badge>
          </>
        )}
      </td>
      <td>
        <div className="row-actions">
          <button
            className="btn btn-sm"
            disabled={control.isPending || (!shouldRun && !camera.enabled)}
            title={!camera.enabled && !shouldRun ? "Enable the camera first" : undefined}
            onClick={() => control.mutate({ id: camera.id, run: !shouldRun }, { onError: (e) => toast.error(e.message) })}
          >
            {shouldRun ? <Square size={14} /> : <Play size={14} />}
            {shouldRun ? "Stop" : "Start"}
          </button>
          <button className="btn btn-sm" disabled={test.isPending} onClick={runTest} title="Check the address, login and codec">
            <PlugZap size={14} /> {test.isPending ? "Testing..." : "Test"}
          </button>
          <button className="btn btn-sm btn-icon" onClick={() => onEdit(camera)} aria-label={`Edit ${camera.name}`} title="Edit">
            <Pencil size={14} />
          </button>
          <button className="btn btn-sm btn-icon btn-danger-ghost" onClick={() => onDelete(camera)} aria-label={`Delete ${camera.name}`} title="Delete">
            <Trash2 size={14} />
          </button>
        </div>
      </td>
    </tr>
  );
}

export function CamerasPage() {
  const cameras = useCameras();
  const fleet = useFleet(cameras.data);
  const remove = useDeleteCamera();
  // `form` is what the drawer shows: closed, a new camera, or one being edited
  const [form, setForm] = useState<{ open: boolean; camera?: Camera }>({ open: false });
  const [toDelete, setToDelete] = useState<Camera | null>(null);

  let body;
  if (cameras.isPending) {
    body = (
      <div className="card panel">
        <Skeleton height={44} />
        <Skeleton height={44} style={{ marginTop: 10 }} />
      </div>
    );
  } else if (!cameras.data) {
    body = <ErrorState title="Failed to load cameras" message={cameras.error?.message} onRetry={() => void cameras.refetch()} />;
  } else if (cameras.data.length === 0) {
    body = (
      <div className="card">
        <EmptyState icon={Cctv} title="No cameras found" hint="Add an IP camera (RTSP) or a video file to start detecting people.">
          <button className="btn btn-primary" onClick={() => setForm({ open: true })}>
            <Plus size={16} /> Add camera
          </button>
        </EmptyState>
      </div>
    );
  } else {
    body = (
      <div className="card table-wrap">
        <table>
          <thead>
            <tr>
              <th>Camera</th>
              <th>Source</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {fleet.map((item) => (
              <Row key={item.camera.id} item={item} onEdit={(c) => setForm({ open: true, camera: c })} onDelete={setToDelete} />
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Cameras</h1>
          <p>Add, change and start the cameras the engine watches.</p>
        </div>
        <button className="btn btn-primary" onClick={() => setForm({ open: true })}>
          <Plus size={16} /> Add camera
        </button>
      </div>
      {body}

      <CameraFormDrawer open={form.open} camera={form.camera} onClose={() => setForm((f) => ({ ...f, open: false }))} />
      <ConfirmDialog
        open={toDelete !== null}
        title={`Delete ${toDelete?.name ?? "camera"}?`}
        confirmLabel="Delete camera"
        busy={remove.isPending}
        error={remove.error?.message}
        onCancel={() => {
          setToDelete(null);
          remove.reset();
        }}
        onConfirm={() => toDelete && remove.mutate(toDelete.id, { onSuccess: () => { toast.success(`${toDelete.name} deleted`); setToDelete(null); } })}
      >
        The camera stops and leaves the list. Its past events and snapshots are kept.
      </ConfirmDialog>
    </div>
  );
}
