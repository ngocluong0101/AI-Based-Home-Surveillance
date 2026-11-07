import { describe, expect, it } from "vitest";
import type { AppSettings } from "./types";
import { describeQuietHours, formatBytes, formFromSettings, isDirty, toSettingsUpdate, validateSettings } from "./utils/settingsForm";
import { emptyForm } from "./utils/cameraForm";

const SAVED: AppSettings = {
  alerts_enabled: true,
  quiet_hours_enabled: false,
  quiet_start: "22:00",
  quiet_end: "06:00",
  snapshot_retention_days: 7,
  default_conf_threshold: 0.25,
  default_absence_timeout: 10,
  telegram_configured: false,
  snapshot_count: 12,
  snapshot_bytes: 3456789,
  version: "0.1.0",
};

describe("the form of the settings", () => {
  it("starts as what is saved, with the numbers as text", () => {
    expect(formFromSettings(SAVED)).toEqual({
      alertsEnabled: true,
      quietHoursEnabled: false,
      quietStart: "22:00",
      quietEnd: "06:00",
      retentionDays: "7",
      confThreshold: "0.25",
      absenceTimeout: "10",
    });
  });

  it("has nothing to send and is not dirty until something changes", () => {
    const form = formFromSettings(SAVED);
    expect(toSettingsUpdate(SAVED, form)).toEqual({});
    expect(isDirty(SAVED, form)).toBe(false);
    expect(isDirty(SAVED, { ...form, alertsEnabled: false })).toBe(true);
  });

  it("sends only what changed, with real numbers", () => {
    const form = { ...formFromSettings(SAVED), alertsEnabled: false, retentionDays: "14", confThreshold: "0.4", absenceTimeout: "20" };
    expect(toSettingsUpdate(SAVED, form)).toEqual({
      alerts_enabled: false,
      snapshot_retention_days: 14,
      default_conf_threshold: 0.4,
      default_absence_timeout: 20,
    });
  });

  it("is not dirty again after a change is typed back", () => {
    const form = { ...formFromSettings(SAVED), retentionDays: "9" };
    expect(isDirty(SAVED, form)).toBe(true);
    expect(isDirty(SAVED, { ...form, retentionDays: "7" })).toBe(false);
    expect(isDirty(SAVED, { ...form, retentionDays: "7.0" })).toBe(false); // 7.0 is 7
  });
});

describe("validating the settings", () => {
  const ok = formFromSettings(SAVED);

  it("accepts what is saved", () => {
    expect(validateSettings(ok)).toEqual({});
  });

  it("checks the times only while the quiet hours are on", () => {
    expect(validateSettings({ ...ok, quietStart: "", quietEnd: "x" })).toEqual({});
    const on = { ...ok, quietHoursEnabled: true };
    expect(validateSettings({ ...on, quietStart: "", quietEnd: "x" })).toEqual({ quietStart: "Enter a time, like 22:00.", quietEnd: "Enter a time, like 06:00." });
    expect(validateSettings({ ...on, quietStart: "25:00" }).quietStart).toBeDefined();
    expect(validateSettings({ ...on, quietEnd: "7:00" }).quietEnd).toBeDefined();
  });

  it("refuses quiet hours that start and end at the same minute", () => {
    expect(validateSettings({ ...ok, quietHoursEnabled: true, quietStart: "08:00", quietEnd: "08:00" }).quietEnd).toBe("The end must differ from the start.");
    expect(validateSettings({ ...ok, quietStart: "08:00", quietEnd: "08:00" })).toEqual({}); // off: any time is fine
  });

  it("wants a whole number of days from 1 to 365", () => {
    for (const bad of ["", "0", "366", "7.5", "abc", "-1"]) expect(validateSettings({ ...ok, retentionDays: bad }).retentionDays).toBeDefined();
    for (const good of ["1", "7", "365"]) expect(validateSettings({ ...ok, retentionDays: good }).retentionDays).toBeUndefined();
  });

  it("wants a confidence from 0 to 1 and seconds from 0 to 86400", () => {
    for (const bad of ["", "1.5", "-0.1", "x"]) expect(validateSettings({ ...ok, confThreshold: bad }).confThreshold).toBeDefined();
    for (const good of ["0", "0.25", "1"]) expect(validateSettings({ ...ok, confThreshold: good }).confThreshold).toBeUndefined();
    for (const bad of ["", "-1", "86401", "y"]) expect(validateSettings({ ...ok, absenceTimeout: bad }).absenceTimeout).toBeDefined();
    for (const good of ["0", "0.2", "86400"]) expect(validateSettings({ ...ok, absenceTimeout: good }).absenceTimeout).toBeUndefined();
  });
});

describe("what the page says in words", () => {
  it("describes quiet hours within a day and past midnight", () => {
    expect(describeQuietHours("13:00", "15:00")).toBe("Alerts are held back from 13:00 until 15:00.");
    expect(describeQuietHours("22:00", "06:00")).toBe("Alerts are held back from 22:00 until 06:00 the next day.");
    expect(describeQuietHours("08:00", "08:00")).toBe("");
    expect(describeQuietHours("", "08:00")).toBe("");
  });

  it("writes sizes the way a person reads them", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(3456789)).toBe("3.3 MB");
    expect(formatBytes(5 * 1024 ** 3)).toBe("5.0 GB");
    expect(formatBytes(150 * 1024 ** 2)).toBe("150 MB");
    expect(formatBytes(-1)).toBe("-");
  });
});

describe("a new camera's form", () => {
  it("starts with what the settings say, else with 0.25 and 10", () => {
    expect(emptyForm({ default_conf_threshold: 0.4, default_absence_timeout: 20 })).toMatchObject({ confThreshold: "0.4", absenceTimeout: "20" });
    expect(emptyForm()).toMatchObject({ confThreshold: "0.25", absenceTimeout: "10" });
  });
});
