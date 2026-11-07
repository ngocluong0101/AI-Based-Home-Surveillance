import type { AppSettings, AppSettingsUpdate } from "../types";

/** The editable settings as the form holds them. The numbers stay text while they are typed; validateSettings checks them. */
export interface SettingsFormValues {
  alertsEnabled: boolean;
  quietHoursEnabled: boolean;
  quietStart: string;
  quietEnd: string;
  retentionDays: string;
  confThreshold: string;
  absenceTimeout: string;
}

export const formFromSettings = (s: AppSettings): SettingsFormValues => ({
  alertsEnabled: s.alerts_enabled,
  quietHoursEnabled: s.quiet_hours_enabled,
  quietStart: s.quiet_start,
  quietEnd: s.quiet_end,
  retentionDays: String(s.snapshot_retention_days),
  confThreshold: String(s.default_conf_threshold),
  absenceTimeout: String(s.default_absence_timeout),
});

const asNumber = (text: string): number => (text.trim() === "" ? NaN : Number(text));
const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

export type SettingsErrors = Partial<Record<"quietStart" | "quietEnd" | "retentionDays" | "confThreshold" | "absenceTimeout", string>>;

/** What is wrong with the form, field by field (the same rules as the backend's); empty = fine. */
export function validateSettings(v: SettingsFormValues): SettingsErrors {
  const errors: SettingsErrors = {};
  if (v.quietHoursEnabled) {
    if (!HHMM.test(v.quietStart)) errors.quietStart = "Enter a time, like 22:00.";
    if (!HHMM.test(v.quietEnd)) errors.quietEnd = "Enter a time, like 06:00.";
    if (!errors.quietStart && !errors.quietEnd && v.quietStart === v.quietEnd) errors.quietEnd = "The end must differ from the start.";
  }
  const days = asNumber(v.retentionDays);
  if (!(Number.isInteger(days) && days >= 1 && days <= 365)) errors.retentionDays = "Enter a whole number of days from 1 to 365.";
  const conf = asNumber(v.confThreshold);
  if (!(conf >= 0 && conf <= 1)) errors.confThreshold = "Enter a number from 0 to 1.";
  const absence = asNumber(v.absenceTimeout);
  if (!(absence >= 0 && absence <= 86400)) errors.absenceTimeout = "Enter a number of seconds from 0 to 86400.";
  return errors;
}

/** Only what differs from what is saved: that is all the backend is sent. */
export function toSettingsUpdate(saved: AppSettings, v: SettingsFormValues): AppSettingsUpdate {
  const body: AppSettingsUpdate = {};
  if (v.alertsEnabled !== saved.alerts_enabled) body.alerts_enabled = v.alertsEnabled;
  if (v.quietHoursEnabled !== saved.quiet_hours_enabled) body.quiet_hours_enabled = v.quietHoursEnabled;
  if (v.quietStart !== saved.quiet_start) body.quiet_start = v.quietStart;
  if (v.quietEnd !== saved.quiet_end) body.quiet_end = v.quietEnd;
  const days = asNumber(v.retentionDays);
  if (days !== saved.snapshot_retention_days) body.snapshot_retention_days = days;
  const conf = asNumber(v.confThreshold);
  if (conf !== saved.default_conf_threshold) body.default_conf_threshold = conf;
  const absence = asNumber(v.absenceTimeout);
  if (absence !== saved.default_absence_timeout) body.default_absence_timeout = absence;
  return body;
}

export const isDirty = (saved: AppSettings, v: SettingsFormValues): boolean => Object.keys(toSettingsUpdate(saved, v)).length > 0;

/** One sentence on what the quiet hours do, or "" while the two times are not usable. */
export function describeQuietHours(start: string, end: string): string {
  if (!HHMM.test(start) || !HHMM.test(end) || start === end) return "";
  return start < end ? `Alerts are held back from ${start} until ${end}.` : `Alerts are held back from ${start} until ${end} the next day.`;
}

/** 1536 -> "1.5 KB". */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "-";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i++;
  }
  return i === 0 ? `${value} B` : `${value.toFixed(value >= 100 ? 0 : 1)} ${units[i]}`;
}
