import type { LucideIcon } from "lucide-react";
import { Bell, HardDrive, Loader2, Send, Server, SlidersHorizontal } from "lucide-react";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";
import { Badge } from "../components/ui/Badge";
import { Skeleton } from "../components/ui/Skeleton";
import { ErrorState } from "../components/ui/States";
import { useEngine, useSettings, useTestNotification, useUpdateSettings } from "../hooks/queries";
import { describeEngine } from "../utils/engine";
import {
  describeQuietHours,
  formatBytes,
  formFromSettings,
  isDirty,
  toSettingsUpdate,
  validateSettings,
  type SettingsErrors,
  type SettingsFormValues,
} from "../utils/settingsForm";

function Card({ id, icon: Icon, title, children }: { id: string; icon: LucideIcon; title: string; children: ReactNode }) {
  return (
    <section className="card panel set-card" aria-labelledby={id}>
      <h2 className="panel-title" id={id}>
        <Icon size={16} aria-hidden /> {title}
      </h2>
      {children}
    </section>
  );
}

function Head() {
  return (
    <div className="page-head">
      <div>
        <h1>Settings</h1>
        <p>Alerts, storage, and what new cameras start with.</p>
      </div>
    </div>
  );
}

/** What the engine as a whole is doing (read-only; the Cameras page has the same note with more words). */
function SystemCard({ version }: { version: string }) {
  const engine = useEngine();
  const note = describeEngine(engine.data);
  return (
    <Card id="set-system" icon={Server} title="System">
      <dl>
        <dt>Engine</dt>
        <dd>
          {engine.isPending ? (
            <Skeleton width={140} />
          ) : engine.isError ? (
            <span className="muted">Could not be read</span>
          ) : note ? (
            <>
              <Badge tone={note.tone}>{note.headline}</Badge>
              {note.places && <span className="muted"> · places in use {note.places}</span>}
            </>
          ) : (
            <span className="muted">Not managed by this backend</span>
          )}
        </dd>
        {note?.detail && (
          <>
            <dt>Next camera</dt>
            <dd>{note.detail}</dd>
          </>
        )}
        <dt>Backend version</dt>
        <dd className="mono">{version}</dd>
      </dl>
      <p className="set-note">The number of engine places is set in the installation (ENGINE_MAX_CAMERAS), because changing it restarts the engine.</p>
    </Card>
  );
}

export function SettingsPage() {
  const settings = useSettings();
  const update = useUpdateSettings();
  const test = useTestNotification();
  const [draft, setDraft] = useState<SettingsFormValues | null>(null);
  const [errors, setErrors] = useState<SettingsErrors>({});

  if (settings.isPending) {
    return (
      <div className="page">
        <Head />
        <div className="settings-grid">
          {[0, 1, 2, 3].map((i) => (
            <div className="card panel" key={i}>
              <Skeleton width={140} height={18} />
              <Skeleton height={44} style={{ marginTop: 14 }} />
              <Skeleton height={44} style={{ marginTop: 10 }} />
            </div>
          ))}
        </div>
      </div>
    );
  }
  if (settings.isError) {
    return (
      <div className="page">
        <Head />
        <ErrorState title="Failed to load the settings" message={settings.error.message} onRetry={() => void settings.refetch()} />
      </div>
    );
  }

  const saved = settings.data;
  const values = draft ?? formFromSettings(saved);
  const dirty = draft !== null && isDirty(saved, draft);

  const set = (patch: Partial<SettingsFormValues>) => {
    setDraft({ ...values, ...patch });
    setErrors({});
    update.reset();
  };
  const discard = () => {
    setDraft(null);
    setErrors({});
    update.reset();
  };
  const save = (e: React.FormEvent) => {
    e.preventDefault();
    const found = validateSettings(values);
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    update.mutate(toSettingsUpdate(saved, values), {
      onSuccess: () => {
        setDraft(null);
        toast.success("Settings saved");
      },
    });
  };
  const sendTest = () =>
    test.mutate(undefined, {
      onSuccess: (r) => (r.ok ? toast.success("Test message sent", { description: r.message }) : toast.error("Test message failed", { description: r.message })),
      onError: (e) => toast.error("The test could not run", { description: e.message }),
    });

  const quietSummary = describeQuietHours(values.quietStart, values.quietEnd);

  return (
    <div className="page">
      <Head />
      <form onSubmit={save} noValidate>
        <div className="settings-grid">
          <Card id="set-notifications" icon={Bell} title="Notifications">
            <div className="set-row">
              <label className="switch">
                <input type="checkbox" checked={values.alertsEnabled} onChange={(e) => set({ alertsEnabled: e.target.checked })} />
                <span>Send Telegram alerts</span>
                <small className="muted">Off: events are still saved and shown here, but no message is sent. Handy while you are home.</small>
              </label>
            </div>
            <div className="set-row">
              <label className="switch">
                <input type="checkbox" checked={values.quietHoursEnabled} onChange={(e) => set({ quietHoursEnabled: e.target.checked })} />
                <span>Quiet hours</span>
                <small className="muted">No alerts during these hours (the server&apos;s local time). Events are still saved.</small>
              </label>
              <div className="quiet-times">
                <label className="field">
                  From
                  <input
                    type="time"
                    value={values.quietStart}
                    disabled={!values.quietHoursEnabled}
                    onChange={(e) => set({ quietStart: e.target.value })}
                    aria-invalid={errors.quietStart ? true : undefined}
                  />
                  {errors.quietStart && <span className="field-error">{errors.quietStart}</span>}
                </label>
                <label className="field">
                  Until
                  <input
                    type="time"
                    value={values.quietEnd}
                    disabled={!values.quietHoursEnabled}
                    onChange={(e) => set({ quietEnd: e.target.value })}
                    aria-invalid={errors.quietEnd ? true : undefined}
                  />
                  {errors.quietEnd && <span className="field-error">{errors.quietEnd}</span>}
                </label>
              </div>
              {values.quietHoursEnabled && quietSummary && <p className="set-note">{quietSummary}</p>}
            </div>
            <div className="set-row">
              <div className="set-inline">
                <Badge tone={saved.telegram_configured ? "ok" : "idle"}>{saved.telegram_configured ? "Telegram connected" : "Telegram not set up"}</Badge>
                <button type="button" className="btn btn-sm" disabled={!saved.telegram_configured || test.isPending} onClick={sendTest}>
                  {test.isPending ? <Loader2 size={14} className="spin" /> : <Send size={14} />}
                  Send test message
                </button>
              </div>
              {!saved.telegram_configured && (
                <p className="set-note">Set ENABLE_TELEGRAM, TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env and restart the backend. The token is never shown here.</p>
              )}
            </div>
          </Card>

          <Card id="set-storage" icon={HardDrive} title="Storage">
            <div className="set-row">
              <label className="field">
                Keep snapshots for (days)
                <input
                  inputMode="numeric"
                  value={values.retentionDays}
                  onChange={(e) => set({ retentionDays: e.target.value })}
                  aria-invalid={errors.retentionDays ? true : undefined}
                />
                {errors.retentionDays && <span className="field-error">{errors.retentionDays}</span>}
              </label>
              <p className="set-note">Older pictures are deleted automatically. The events stay, without their picture.</p>
            </div>
            <dl>
              <dt>In use now</dt>
              <dd>
                {saved.snapshot_count.toLocaleString("en-US")} {saved.snapshot_count === 1 ? "snapshot" : "snapshots"} · {formatBytes(saved.snapshot_bytes)}
              </dd>
            </dl>
          </Card>

          <Card id="set-new-cameras" icon={SlidersHorizontal} title="New cameras">
            <div className="set-row">
              <div className="field-row">
                <label className="field">
                  Minimum confidence (0-1)
                  <input
                    inputMode="decimal"
                    value={values.confThreshold}
                    onChange={(e) => set({ confThreshold: e.target.value })}
                    aria-invalid={errors.confThreshold ? true : undefined}
                  />
                  {errors.confThreshold && <span className="field-error">{errors.confThreshold}</span>}
                </label>
                <label className="field">
                  Report the same person again after (s)
                  <input
                    inputMode="decimal"
                    value={values.absenceTimeout}
                    onChange={(e) => set({ absenceTimeout: e.target.value })}
                    aria-invalid={errors.absenceTimeout ? true : undefined}
                  />
                  {errors.absenceTimeout && <span className="field-error">{errors.absenceTimeout}</span>}
                </label>
              </div>
              <p className="set-note">The Add camera form starts with these two. Cameras you already have keep their own values.</p>
            </div>
          </Card>

          <SystemCard version={saved.version} />
        </div>

        <div className="save-bar">
          <span className={`save-status ${dirty ? "dirty" : "muted"}`} role="status">
            {dirty ? "You have unsaved changes." : "All changes saved."}
          </span>
          {update.isError && (
            <span className="err-text" role="alert">
              {update.error.message}
            </span>
          )}
          <button type="button" className="btn" disabled={!dirty || update.isPending} onClick={discard}>
            Discard
          </button>
          <button type="submit" className="btn btn-primary" disabled={!dirty || update.isPending}>
            {update.isPending && <Loader2 size={16} className="spin" />}
            Save changes
          </button>
        </div>
      </form>
    </div>
  );
}
