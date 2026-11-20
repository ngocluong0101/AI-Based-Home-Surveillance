import { Loader2, PlugZap } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { api } from "../api";
import { useCreateCamera, useLatestSnapshot, useSettings, useTestConnection, useUpdateCamera } from "../hooks/queries";
import type { Camera } from "../types";
import {
  emptyForm,
  formFromCamera,
  toConnectionBody,
  toCreatePayload,
  toUpdatePayload,
  validateForm,
  type CameraFormValues,
  type FormErrors,
} from "../utils/cameraForm";
import type { BehaviorName, FallNet } from "../types";
import { ConnectionResult } from "./ConnectionResult";
import { ZoneEditor } from "./ZoneEditor";
import { Drawer } from "./ui/Drawer";

function Form({ camera, onDone }: { camera?: Camera; onDone: () => void }) {
  const settings = useSettings(); // warmed by the layout, so it is there when the form opens
  const [values, setValues] = useState<CameraFormValues>(() => (camera ? formFromCamera(camera) : emptyForm(settings.data)));
  const [errors, setErrors] = useState<FormErrors>({});
  const create = useCreateCamera();
  const update = useUpdateCamera();
  const test = useTestConnection();
  const saving = create.isPending || update.isPending;
  const snapshot = useLatestSnapshot(camera?.id ?? -1, camera !== undefined); // a picture to draw the zones on
  const serverError = (create.error ?? update.error)?.message;

  const set = <K extends keyof CameraFormValues>(key: K, value: CameraFormValues[K]) => {
    setValues((v) => ({ ...v, [key]: value }));
    setErrors({});
    test.reset();
  };

  const toggle = (name: BehaviorName, on: boolean) => {
    const next = on ? [...values.behaviors.filter((b) => b !== name), name] : values.behaviors.filter((b) => b !== name);
    set("behaviors", next);
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const found = validateForm(values, camera);
    setErrors(found);
    if (Object.keys(found).length > 0) return;

    if (camera) {
      const body = toUpdatePayload(values, camera);
      if (Object.keys(body).length === 0) return onDone();
      update.mutate({ id: camera.id, body }, { onSuccess: () => { toast.success("Camera saved"); onDone(); } });
    } else {
      create.mutate(toCreatePayload(values), {
        onSuccess: (created) => {
          onDone();
          toast.success(`${created.name} added`, {
            description: created.enabled ? "It is stopped until you start it." : "It is disabled.",
            action: created.enabled
              ? { label: "Start", onClick: () => void api.startCamera(created.id).then(() => toast.success("Starting...")) }
              : undefined,
          });
        },
      });
    }
  };

  const runTest = () => {
    const found = validateForm(values, camera);
    const { name: _n, confThreshold: _c, absenceTimeout: _a, ...addressErrors } = found;   // the test needs only the address
    setErrors(addressErrors);
    if (Object.keys(addressErrors).length > 0) return;
    const body = toConnectionBody(values, camera);
    if (body) test.mutate(body);
  };
  const canTest = toConnectionBody(values, camera) !== null;
  const isRtsp = values.sourceType === "rtsp";

  return (
    <form onSubmit={submit} noValidate style={{ display: "contents" }}>
      <div className="drawer-body">
        <label className="field wide">
          Name
          <input value={values.name} onChange={(e) => set("name", e.target.value)} placeholder="Front door" maxLength={100} autoFocus aria-invalid={errors.name ? true : undefined} />
          {errors.name && <span className="field-error">{errors.name}</span>}
        </label>

        <div className="field wide">
          Source
          <div className="segmented" role="radiogroup" aria-label="Source type">
            {(["rtsp", "file"] as const).map((t) => (
              <button
                type="button"
                key={t}
                role="radio"
                aria-checked={values.sourceType === t}
                className={values.sourceType === t ? "on" : ""}
                onClick={() => set("sourceType", t)}
              >
                {t === "rtsp" ? "IP camera (RTSP)" : "Video file"}
              </button>
            ))}
          </div>
        </div>

        <label className="field wide">
          {isRtsp ? "Camera address" : "File path"}
          <input
            value={values.sourceUri}
            onChange={(e) => set("sourceUri", e.target.value)}
            placeholder={camera ? "Leave empty to keep the saved address" : isRtsp ? "rtsp://192.168.1.10:554/stream1" : "file:///data/videos/office.mp4"}
            spellCheck={false}
            autoComplete="off"
            className="mono"
            aria-invalid={errors.sourceUri ? true : undefined}
          />
          {camera && (
            <span className="field-hint">Currently {camera.source_uri}. The saved password is never shown.</span>
          )}
          {errors.sourceUri && <span className="field-error">{errors.sourceUri}</span>}
        </label>

        {isRtsp && (
          <div className="field-row">
            <label className="field">
              Username (optional)
              <input value={values.username} onChange={(e) => set("username", e.target.value)} autoComplete="off" />
            </label>
            <label className="field">
              Password (optional)
              <input type="password" value={values.password} onChange={(e) => set("password", e.target.value)} autoComplete="new-password" />
            </label>
            {errors.login && <span className="field-error wide">{errors.login}</span>}
            <span className="field-hint wide">Typed here, special characters need no escaping. The password is stored on the server and shown nowhere.</span>
          </div>
        )}

        <label className="switch">
          <input type="checkbox" checked={values.enabled} onChange={(e) => set("enabled", e.target.checked)} />
          <span>Enabled</span>
          <small className="muted">A disabled camera cannot be started.</small>
        </label>

        <div className="field-row">
          <label className="field">
            Minimum confidence (0-1)
            <input
              inputMode="decimal"
              value={values.confThreshold}
              onChange={(e) => set("confThreshold", e.target.value)}
              aria-invalid={errors.confThreshold ? true : undefined}
            />
            {errors.confThreshold && <span className="field-error">{errors.confThreshold}</span>}
          </label>
          <label className="field">
            Report the same person again after (s)
            <input
              inputMode="decimal"
              value={values.absenceTimeout}
              onChange={(e) => set("absenceTimeout", e.target.value)}
              aria-invalid={errors.absenceTimeout ? true : undefined}
            />
            {errors.absenceTimeout && <span className="field-error">{errors.absenceTimeout}</span>}
          </label>
          <span className="field-hint wide">
            Detections less certain than the minimum are ignored for this camera. The detector itself never reports anything under
            0.25, so a lower value changes nothing. A person who leaves and comes back sooner than the time on the right does not raise a new alert.
          </span>
        </div>

        <label className="switch">
          <input type="checkbox" checked={values.aiEnabled} onChange={(e) => set("aiEnabled", e.target.checked)} />
          <span>Detect people</span>
          <small className="muted">Off: the picture is still shown, but nobody is detected, counted or reported.</small>
        </label>
        {camera && (
          <span className="field-hint">A change to these three is applied within a second or two, without interrupting any camera.</span>
        )}

        <fieldset className="rules">
          <legend>Safety rules</legend>
          <label className="switch">
            <input type="checkbox" checked={values.behaviors.includes("fall")} onChange={(e) => toggle("fall", e.target.checked)} />
            <span>Detect falls</span>
            <small className="muted">An alert when someone falls and does not get up. Needs the pose model.</small>
          </label>
          {values.behaviors.includes("fall") && (
            <label className="field">
              How a fall is decided
              <select value={values.fallNet} onChange={(e) => set("fallNet", Number(e.target.value) as FallNet)}>
                <option value={0}>Rules (the person is on the floor and stays there)</option>
                <option value={1}>Skeleton network (learned from falls)</option>
                <option value={2}>Network or rules (an alert from either)</option>
              </select>
              <span className="field-hint">
                The network reacts about a second sooner and raised far fewer false alarms in the lab tests, but it was learned from one
                room; the rules are the safe default. Without the model file the engine uses the rules.
              </span>
            </label>
          )}
          {values.behaviors.includes("fall") && values.fallNet !== 0 && (
            <div className="field-row">
              <label className="field">
                Network score that counts as a fall
                <input
                  inputMode="decimal"
                  value={values.fallNetThr}
                  onChange={(e) => set("fallNetThr", e.target.value)}
                  aria-invalid={errors.fallNetThr ? true : undefined}
                />
                {errors.fallNetThr && <span className="field-error">{errors.fallNetThr}</span>}
              </label>
              <label className="field">
                Scores in a row needed
                <input
                  inputMode="numeric"
                  value={values.fallNetK}
                  onChange={(e) => set("fallNetK", e.target.value)}
                  aria-invalid={errors.fallNetK ? true : undefined}
                />
                {errors.fallNetK && <span className="field-error">{errors.fallNetK}</span>}
              </label>
              <span className="field-hint wide">
                Higher score or more scores in a row: fewer false alarms, a slightly later alert. 0.9 and 3 are the defaults; 0.95 gave
                the fewest false alarms on the lab data (UR Fall), which is one room.
              </span>
            </div>
          )}
          <label className="switch">
            <input type="checkbox" checked={values.behaviors.includes("fight")} onChange={(e) => toggle("fight", e.target.checked)} />
            <span>Detect fights</span>
            <small className="muted">An alert when people hit each other. Needs the pose model.</small>
          </label>
          <label className="switch">
            <input type="checkbox" checked={values.behaviors.includes("zones")} onChange={(e) => toggle("zones", e.target.checked)} />
            <span>Alert in restricted areas</span>
            <small className="muted">An alert when someone stays in a restricted zone for a few seconds.</small>
          </label>
          <ZoneEditor zones={values.zones} onChange={(z) => set("zones", z)} background={snapshot.url} />
          {errors.zones && <span className="field-error">{errors.zones}</span>}
          <span className="field-hint">
            A bed or sofa zone stops lying on it from counting as a fall; an ignore zone hides a TV or a mirror from every rule.
            {camera && " Changes are applied within a second or two, without interrupting any camera."}
          </span>
        </fieldset>

        <div className="test-box">
          <button type="button" className="btn" disabled={!canTest || test.isPending} onClick={runTest}>
            {test.isPending ? <Loader2 size={16} className="spin" /> : <PlugZap size={16} />}
            Test connection
          </button>
          {!canTest && <span className="muted">{camera ? "Change the source type or enter an address to test." : "Enter an address to test it."}</span>}
          {test.isError && <p className="err-text">The test could not run: {test.error.message}</p>}
          {test.data && <ConnectionResult result={test.data} />}
        </div>

        {serverError && <p className="err-text" role="alert">{serverError}</p>}
      </div>
      <div className="drawer-foot">
        <button type="button" className="btn" onClick={onDone}>
          Cancel
        </button>
        <button type="submit" className="btn btn-primary" disabled={saving}>
          {saving && <Loader2 size={16} className="spin" />}
          {camera ? "Save changes" : "Add camera"}
        </button>
      </div>
    </form>
  );
}

/** Add (no `camera`) or edit a camera. `open` false closes it; the form starts fresh each time it opens. */
export function CameraFormDrawer({ open, camera, onClose }: { open: boolean; camera?: Camera; onClose: () => void }) {
  return (
    <Drawer open={open} title={camera ? `Edit ${camera.name}` : "Add camera"} onClose={onClose}>
      <Form key={camera?.id ?? "new"} camera={camera} onDone={onClose} />
    </Drawer>
  );
}
